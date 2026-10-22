#!/usr/bin/env bash
# V1.1 single local lifecycle controller. Derived from the recovered V1 helper.
# Cold-state and supervisor readiness are technical gates, not business acceptance.
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then set -Eeuo pipefail; fi
umask 077
STATE_ROOT="${GREEN_V1_SERVICE_STATE:-/run/green-v1-services}"
WAIT_STEP="${GREEN_V1_WAIT_STEP:-5}"
STOP_WAIT="${GREEN_V1_STOP_WAIT:-90}"
START_WAIT="${GREEN_V1_START_WAIT:-180}"
MONIT_TIMEOUT="${GREEN_V1_MONIT_TIMEOUT:-15}"
XR_PORTS="${GREEN_V1_XROCKET_PORTS:-7001 27001}"
XR_WAIT="${GREEN_V1_XROCKET_WAIT:-60}"
PRODUCT_CLI="${GREEN_V1_PRODUCT_CLI:-/opt/data/xrocket/xrocket.v2}"
MONITRC="${GREEN_V1_MONITRC:-/etc/monitrc}"
COMMAND_LOCK_HELD=0

say() { printf '%s\n' "$*"; }
fail() { say "BLOCKED: $*" >&2; return 2; }
need() { command -v "$1" >/dev/null 2>&1 || fail "missing $1"; }
root_only() { [[ $(id -u) == 0 ]] || fail 'root required'; }
hostname_proc() { local h; if [[ -r /proc/sys/kernel/hostname ]] && IFS= read -r h < /proc/sys/kernel/hostname; then printf '%s' "$h"; else hostname; fi; }
host_identity() {
  local boot=unavailable
  [[ ! -r /proc/sys/kernel/random/boot_id ]] || IFS= read -r boot < /proc/sys/kernel/random/boot_id
  printf '%s|%s' "$(hostname_proc)" "$boot"
}
validate_token() { [[ ${1:-} =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$ ]] || fail 'invalid session token'; }
validate_waits() {
  [[ $WAIT_STEP =~ ^[1-9][0-9]*$ && $STOP_WAIT =~ ^[0-9]+$ && $START_WAIT =~ ^[0-9]+$ && $XR_WAIT =~ ^[0-9]+$ ]] || { fail 'invalid lifecycle wait interval'; return; }
  [[ $MONIT_TIMEOUT =~ ^[1-9][0-9]{0,2}$ ]] && ((MONIT_TIMEOUT<=300)) || fail 'invalid supervisor command timeout (1..300 seconds)'
}
monit_call() { timeout -k 2 "$MONIT_TIMEOUT" monit "$@"; }
require_monit() { need monit && monit_call summary >/dev/null || fail 'monit summary unavailable'; }
check_single_node() {
  [[ -r $MONITRC && -x $PRODUCT_CLI ]] || { fail 'missing single-node product/Monit installation'; return; }
  local n rc
  for n in keepalived xactivestandby; do
    rc=0; pgrep -x "$n" >/dev/null 2>&1 || rc=$?
    [[ $rc == 1 ]] || { fail "HA process detected or process scan failed: $n"; return; }
  done
}
process_identity() {
  local pid=$1 identity
  [[ $pid =~ ^[1-9][0-9]*$ ]] || return 1
  if [[ -r /proc/$pid/stat ]]; then
    # Strip the parenthesized comm, which may itself contain spaces.
    sed 's/^[^(]*(.*) //' "/proc/$pid/stat" | awk '{print $20}'
  else
    identity=$(ps -p "$pid" -o lstart= 2>/dev/null) || return 1
    [[ -n $identity ]] || return 1
    printf '%s\n' "$identity"
  fi
}
ensure_state() {
  [[ ! -L $STATE_ROOT ]] || { fail 'state root is a symlink'; return; }
  mkdir -p -- "$STATE_ROOT/sessions" || return
  [[ ! -L $STATE_ROOT/sessions ]] || { fail 'session root is a symlink'; return; }
  chmod 700 "$STATE_ROOT" "$STATE_ROOT/sessions"
}
release_command_lock() {
  if [[ $COMMAND_LOCK_HELD == 1 ]]; then
    rm -f -- "$STATE_ROOT/.command-lock/owner"
    rmdir -- "$STATE_ROOT/.command-lock" || true
    COMMAND_LOCK_HELD=0
  fi
}
acquire_command_lock() {
  local identity
  ensure_state || return
  identity=$(process_identity "$$") && [[ -n $identity ]] || { fail 'command owner process identity unavailable'; return; }
  if ! mkdir -- "$STATE_ROOT/.command-lock" 2>/dev/null; then
    fail 'lifecycle command lock exists; reconcile owner host/PID/start identity before manual recovery'; return
  fi
  COMMAND_LOCK_HELD=1
  printf '%s\n%s\n%s\n' "$(host_identity)" "$$" "$identity" > "$STATE_ROOT/.command-lock/owner"
  trap release_command_lock EXIT
  trap 'say LIFECYCLE_INTERRUPTED_TOKEN_RETAINED >&2; exit 130' INT
  trap 'say LIFECYCLE_INTERRUPTED_TOKEN_RETAINED >&2; exit 143' TERM HUP
}

# Parse delimited Monit/Gonit by header columns. A Gonit Cluster value such as
# "Fail" is separate from the multiword Local value "Does not exist".
monit_process_rows() {
  awk '
    function trim(s) {gsub(/^[ \t]+|[ \t]+$/, "", s); return s}
    {
      gsub(/\r/, ""); gsub(/│/, "|")
      if (index($0,"|")) {
        line=trim($0); sub(/^\|/,"",line); sub(/\|$/,"",line)
        k=split(line,b,"|"); for(i=1;i<=k;i++) b[i]=trim(b[i])
        if(b[1]=="Service Name") {
          format=""; status_col=0; type_col=0
          for(i=2;i<=k;i++) {if(b[i]=="Local" || b[i]=="Status") status_col=i; if(b[i]=="Type") type_col=i}
          if(status_col && type_col) format="delimited"
          next
        }
        is_process=0; for(i=1;i<=k;i++) if(b[i]=="Process") is_process=1
        if(is_process) {
          if(format=="delimited" && b[1]~/^[A-Za-z0-9_.-]+$/ && b[type_col]=="Process") print b[1] "\t" b[status_col]
          else print "UNPARSEABLE_ROW\tUNRECOGNIZED"
        }
        next
      }
      if($1=="Service" && $2=="Name") {
        if($3=="Cluster" && $4=="Local" && $5=="Type") format="gonit"
        else if($3=="Status" && $4=="Type") format="monit"
        else format=""
        next
      }
      if(format!="monit" && format!="gonit") next
      type_pos=0; for(i=2;i<=NF;i++) if($i=="Process") {type_pos=i;break}
      if(!type_pos) next
      if($1 !~ /^[A-Za-z0-9_.-]+$/) {print "UNPARSEABLE_ROW\tUNRECOGNIZED"; next}
      start=(format=="gonit" ? 3 : 2); status="UNRECOGNIZED"
      if(type_pos>start) {status=$start;for(i=start+1;i<type_pos;i++)status=status " " $i}
      print $1 "\t" status
    }' "${1:--}"
}
monit_snapshot() {
  monit_call summary > "$1" || return 1
  monit_process_rows "$1" | awk -F '\t' '
    {if(seen[$1]++) bad=1; n++} END{exit (n==0 || bad)}'
}
list_all() { monit_process_rows "$1" | cut -f1 | LC_ALL=C sort; }
list_running() { monit_process_rows "$1" | awk -F '\t' '$2=="OK" || $2=="Running" {print $1}' | LC_ALL=C sort; }
list_unknown() {
  monit_process_rows "$1" | awk -F '\t' '$2!="OK" && $2!="Running" && $2!="Stopped" && $2!="Not monitored" && $2!="Does not exist" {print $1 "=" $2}'
}
writer_check() {
  local name rc bad=0
  for name in mysqld mariadbd beam.smp redis-server ssdb-server mongod postgres kingbase nats-server etcd; do
    rc=0; pgrep -x "$name" >/dev/null 2>&1 || rc=$?
    if [[ $rc == 0 ]]; then say "WRITER_RUNNING=$name"; bad=1
    elif [[ $rc != 1 ]]; then say "WRITER_SCAN_FAILED=$name"; bad=1; fi
  done
  # Linux comm is limited to 15 characters; match the full executable token.
  rc=0; pgrep -f '^([^[:space:]]*/)?clickhouse-server([[:space:]]|$)' >/dev/null 2>&1 || rc=$?
  if [[ $rc == 0 ]]; then say WRITER_RUNNING=clickhouse-server; bad=1
  elif [[ $rc != 1 ]]; then say WRITER_SCAN_FAILED=clickhouse-server; bad=1; fi
  if [[ -n ${GREEN_V1_WRITER_NAMES_FILE:-} ]]; then
    [[ -f $GREEN_V1_WRITER_NAMES_FILE && ! -L $GREEN_V1_WRITER_NAMES_FILE ]] || { fail 'invalid extra writer inventory'; return; }
    while IFS= read -r name || [[ -n $name ]]; do
      [[ $name =~ ^[A-Za-z0-9_.-]+$ ]] || { fail 'invalid extra writer name'; return; }
      rc=0; pgrep -f "^([^[:space:]]*/)?${name//./[.]}([[:space:]]|$)" >/dev/null 2>&1 || rc=$?
      if [[ $rc != 1 ]]; then say "EXTRA_WRITER_ACTIVE_OR_SCAN_FAILED=$name"; bad=1; fi
    done < "$GREEN_V1_WRITER_NAMES_FILE"
  fi
  return "$bad"
}
source_is_quiet() {
  local snap running unknown
  snap=$(mktemp "$STATE_ROOT/quiet.XXXXXXXX") || return
  if ! monit_snapshot "$snap"; then rm -f -- "$snap"; say MONIT_INVENTORY_UNAVAILABLE; return 1; fi
  running=$(list_running "$snap"); unknown=$(list_unknown "$snap")
  rm -f -- "$snap"
  [[ -z $unknown ]] || { say "MONIT_UNSETTLED=$unknown"; return 1; }
  [[ -z $running ]] || { say "MANAGED_RUNNING=$running"; return 1; }
  writer_check
}
cold_check() {
  source_is_quiet || return 1
  sleep "$WAIT_STEP"
  source_is_quiet || return 1
  say COLD_STABLE_PASS
}
session_dir() { printf '%s/sessions/%s' "$STATE_ROOT" "$1"; }
check_active() {
  local token=$1 role=${2:-} dir
  validate_token "$token" || return
  [[ -f $STATE_ROOT/active && ! -L $STATE_ROOT/active ]] || { fail 'no active lifecycle session'; return; }
  [[ $(cat "$STATE_ROOT/active") == "$token" ]] || { fail 'different lifecycle token is active'; return; }
  dir=$(session_dir "$token")
  [[ -d $dir && ! -L $dir && -s $dir/before.monit && -s $dir/all.txt && -f $dir/running.txt && -s $dir/host && -s $dir/role ]] || { fail 'saved lifecycle prestate incomplete'; return; }
  [[ $(cat "$dir/host") == "$(host_identity)" ]] || { fail 'session host/boot identity changed'; return; }
  [[ -z $role || $(cat "$dir/role") == "$role" ]] || { fail 'source/target session role mismatch'; return; }
}
diagnose_service() {
  # Never read pidfiles, delete locks, or print supervisor logs (may hold secrets).
  local name=$1 snap=$2 status
  status=$(monit_process_rows "$snap" | awk -F '\t' -v n="$name" '$1==n {print $2}')
  say "SERVICE_NOT_READY=$name STATUS=${status:-MISSING} DIAG=inspect_supervisor_status_and_pidfile_lock_manually"
}
stop_inventory() {
  local all=$1 svc bad=0
  while IFS= read -r svc; do
    [[ -n $svc ]] || continue
    monit_call stop "$svc" >/dev/null 2>&1 || { say "STOP_REQUEST_FAILED=$svc"; bad=1; }
  done < "$all"
  return "$bad"
}
wait_cold() {
  local elapsed
  for ((elapsed=0; elapsed<=STOP_WAIT; elapsed+=WAIT_STEP)); do
    if cold_check; then sync; return 0; fi
    ((elapsed>=STOP_WAIT)) || sleep "$WAIT_STEP"
  done
  say COLD_GATE_FAILED_TOKEN_RETAINED
  return 3
}
freeze() {
  local role=$1 token=$2 yes=${3:-} dir stage unknown answer
  validate_token "$token" && check_single_node && require_monit || return
  if [[ $role == target && -e ${GREEN_V11_COPY_LOCK_ROOT:-/run/green-v11-copy} ]]; then
    fail 'source copy owns this host; target lifecycle is blocked'; return
  fi
  if [[ $yes != --yes ]]; then
    [[ -z $yes && -t 0 ]] || { fail 'explicit --yes or interactive FREEZE confirmation required'; return; }
    printf 'Type FREEZE to stop inventoried %s services: ' "$role"
    IFS= read -r answer
    [[ $answer == FREEZE ]] || { fail 'no freeze confirmation'; return; }
  fi
  [[ ! -e $STATE_ROOT/active ]] || { fail 'active lifecycle session exists; resume/reconcile it first'; return; }
  dir=$(session_dir "$token")
  [[ ! -e $dir ]] || { fail 'token was already used; use a new token or resume existing session'; return; }
  stage=$(mktemp -d "$STATE_ROOT/sessions/.pending.XXXXXXXX") || return
  if ! monit_snapshot "$stage/before.monit"; then rm -rf -- "$stage"; fail 'no supervisor inventory; no stop issued'; return; fi
  unknown=$(list_unknown "$stage/before.monit")
  if [[ -n $unknown ]]; then rm -rf -- "$stage"; fail "unsettled supervisor inventory: $unknown; no stop issued"; return; fi
  list_all "$stage/before.monit" > "$stage/all.txt"
  list_running "$stage/before.monit" > "$stage/running.txt"
  host_identity > "$stage/host"; printf '%s\n' "$role" > "$stage/role"
  : > "$stage/started.txt"
  mv -- "$stage" "$dir" || return
  printf '%s\n' "$token" > "$STATE_ROOT/.active.$$"
  mv -- "$STATE_ROOT/.active.$$" "$STATE_ROOT/active" || return
  sync
  say "${role^^}_FREEZE_STARTED=$token"
  stop_inventory "$dir/all.txt" || return 3
  wait_cold || return
  printf '%s\n' frozen > "$dir/phase"
  say "${role^^}_FREEZE_PASS=$token"
}
check_session_cold() {
  local role=$1 token=$2
  check_active "$token" "$role" && cold_check || return
  say "${role^^}_QUIET_PASS"
}
source_transfer_begin() {
  local token=$1 pid=$2 dir identity
  check_active "$token" source && cold_check || return
  dir=$(session_dir "$token")
  [[ ! -e $dir/transfer.lease ]] || { fail 'transfer lease already exists'; return; }
  identity=$(process_identity "$pid") && [[ -n $identity ]] || { fail 'transfer owner PID identity unavailable'; return; }
  printf '%s\n%s\n%s\n' "$(host_identity)" "$pid" "$identity" > "$dir/.transfer.$$"
  mv -- "$dir/.transfer.$$" "$dir/transfer.lease"
  say SOURCE_TRANSFER_LEASE_ACTIVE
}
source_transfer_end() {
  local token=$1 pid=$2 dir lines identity
  check_active "$token" source || return
  dir=$(session_dir "$token")
  [[ -f $dir/transfer.lease && ! -L $dir/transfer.lease ]] || { fail 'missing transfer lease'; return; }
  mapfile -t lines < "$dir/transfer.lease"
  [[ ${#lines[@]} == 3 && ${lines[0]} == "$(host_identity)" && ${lines[1]} == "$pid" ]] || { fail 'transfer owner mismatch'; return; }
  identity=$(process_identity "$pid") && [[ $identity == "${lines[2]}" ]] || { fail 'transfer owner process identity changed/unavailable; reconcile lease manually'; return; }
  # The caller must first terminate/wait for every transfer child. A dead PID
  # alone never authorizes resume; an explicit matching lease release is needed.
  rm -f -- "$dir/transfer.lease"
  say SOURCE_TRANSFER_LEASE_RELEASED
}
transfer_absent() {
  local dir=$1
  [[ ! -e $dir/transfer.lease ]] || { fail 'transfer lease retained; wait/cancel transfer and release matching lease before resume'; return; }
  local copy=${GREEN_V11_COPY_LOCK_ROOT:-/run/green-v11-copy} owner
  if [[ -e $copy ]]; then
    [[ -f $copy/owner && ! -L $copy/owner ]] || { fail 'copy owner unavailable; retain source state'; return; }
    IFS= read -r owner < "$copy/owner"
    [[ $owner == "$(cat "$STATE_ROOT/active")|$PPID" ]] || { fail 'source copy owns this host; only its completed owner may resume'; return; }
  fi
  # The backup owner shell may still have backup-to-relay in argv after every
  # synchronous copy child was waited and its lease released. Do not confuse
  # that shell with an active transfer by matching a generic command prefix.
}
validate_desired() {
  local input=$1 all=$2 output=$3 svc
  [[ -f $input && ! -L $input ]] || { fail 'desired-state file missing or symlink'; return; }
  : > "$output"
  while IFS= read -r svc || [[ -n $svc ]]; do
    [[ $svc =~ ^[A-Za-z0-9_.-]+$ ]] && grep -Fxq -- "$svc" "$all" || { fail 'desired service absent from saved inventory'; return; }
    printf '%s\n' "$svc" >> "$output"
  done < "$input"
  LC_ALL=C sort -u "$output" -o "$output"
}
port_listens() {
  local port=$1
  [[ $port =~ ^[0-9]{1,5}$ ]] || return 1
  if command -v ss >/dev/null 2>&1; then ss -lnt 2>/dev/null | awk -v p="$port" '$4~(":"p"$"){ok=1} END{exit !ok}'
  elif command -v netstat >/dev/null 2>&1; then netstat -lnt 2>/dev/null | awk -v p="$port" '$4~(":"p"$"){ok=1} END{exit !ok}'
  else return 1; fi
}
xrocket_verify() {
  local expected=$1 elapsed port all
  grep -Fxq xrocket.v2 "$expected" || return 0
  for ((elapsed=0; elapsed<=XR_WAIT; elapsed+=WAIT_STEP)); do
    all=1; for port in $XR_PORTS; do port_listens "$port" || all=0; done
    [[ $all == 1 ]] && { say XROCKET_LISTENERS_PASS; return 0; }
    ((elapsed>=XR_WAIT)) || sleep "$WAIT_STEP"
  done
  say XROCKET_LISTENERS_FAILED_BUSINESS_ACCEPTANCE_PENDING
  return 1
}
state_matches() {
  local desired=$1 all=$2 snap=$3 actual expected_inventory unknown
  monit_snapshot "$snap" || return 1
  unknown=$(list_unknown "$snap"); [[ -z $unknown ]] || return 1
  expected_inventory=$(cat "$all"); actual=$(list_all "$snap")
  [[ $actual == "$expected_inventory" ]] || return 1
  actual=$(list_running "$snap")
  [[ $actual == "$(cat "$desired")" ]]
}
apply_desired() {
  local dir=$1 desired=$2 svc elapsed snap="$1/after.monit"
  local current="$1/resume-before.monit" running="$1/resume-running.txt"
  monit_snapshot "$current" || { fail 'cannot inventory services before resume'; return; }
  [[ -z $(list_unknown "$current") ]] || { say SERVICES_UNSETTLED_NO_START; return 4; }
  [[ $(list_all "$current") == "$(cat "$dir/all.txt")" ]] || { say SERVICES_INVENTORY_CHANGED_NO_START; return 4; }
  list_running "$current" > "$running"
  # Persist every requested start before invoking a wrapper, including failures.
  # Recovery therefore also stops a wrapper which starts a child then returns nonzero.
  while IFS= read -r svc; do
    grep -Fxq -- "$svc" "$desired" && continue
    grep -Fxq -- "$svc" "$running" || continue
    monit_call stop "$svc" >/dev/null 2>&1 || say "STOP_REQUEST_WARNING=$svc"
  done < "$dir/all.txt"
  for svc in tools_mariadb tools_redis tools_ssdb tools_rabbitmq tools_nats tools_nats_js tools_nginx xetcd; do
    if grep -Fxq -- "$svc" "$desired"; then
      grep -Fxq -- "$svc" "$running" && continue
      printf '%s\n' "$svc" >> "$dir/started.txt"
      monit_call start "$svc" >/dev/null 2>&1 || say "START_REQUEST_WARNING=$svc"
    fi
  done
  while IFS= read -r svc; do
    case "$svc" in tools_mariadb|tools_redis|tools_ssdb|tools_rabbitmq|tools_nats|tools_nats_js|tools_nginx|xetcd) continue;; esac
    grep -Fxq -- "$svc" "$running" && continue
    printf '%s\n' "$svc" >> "$dir/started.txt"
    monit_call start "$svc" >/dev/null 2>&1 || say "START_REQUEST_WARNING=$svc"
  done < "$desired"
  for ((elapsed=0; elapsed<=START_WAIT; elapsed+=WAIT_STEP)); do
    if state_matches "$desired" "$dir/all.txt" "$snap"; then
      sleep "$WAIT_STEP"
      if state_matches "$desired" "$dir/all.txt" "$snap" && xrocket_verify "$desired"; then return 0; fi
    fi
    ((elapsed>=START_WAIT)) || sleep "$WAIT_STEP"
  done
  while IFS= read -r svc; do diagnose_service "$svc" "$snap"; done < "$desired"
  say SERVICE_RECOVERY_PARTIAL_TOKEN_RETAINED
  return 4
}
complete_session() {
  local token=$1 dir
  dir=$(session_dir "$token")
  printf '%s\n' completed > "$dir/phase"
  cp -- "$STATE_ROOT/active" "$STATE_ROOT/.completed.$$"
  mv -- "$STATE_ROOT/.completed.$$" "$STATE_ROOT/completed"
  rm -f -- "$STATE_ROOT/active"
}
resume() {
  local role=$1 token=$2 option=${3:-} profile=${4:-} dir desired
  validate_token "$token" || return
  if [[ ! -e $STATE_ROOT/active && -f $STATE_ROOT/completed && $(cat "$STATE_ROOT/completed") == "$token" ]]; then
    [[ -f $(session_dir "$token")/role && $(cat "$(session_dir "$token")/role") == "$role" ]] || { fail 'completed session role mismatch'; return; }
    say "${role^^}_RESUME_ALREADY_PASS=$token"; return 0
  fi
  check_active "$token" "$role" && check_single_node && require_monit || return
  dir=$(session_dir "$token"); desired="$dir/desired.txt"
  [[ $role != source ]] || transfer_absent "$dir" || return
  if [[ -n $option ]]; then
    [[ $option == --desired-file && -n $profile ]] || { fail 'resume TOKEN [--desired-file FILE]'; return; }
    validate_desired "$profile" "$dir/all.txt" "$dir/.desired.$$" || return
  else cp -- "$dir/running.txt" "$dir/.desired.$$"; fi
  mv -- "$dir/.desired.$$" "$desired"
  printf '%s\n' resuming > "$dir/phase"
  apply_desired "$dir" "$desired" || return
  if [[ $role == source && -n $option ]]; then
    printf '%s\n' profile-recovered > "$dir/phase"
    say "SOURCE_RECOVERED_PROFILE=$token FULL_ORIGINAL_RETAINED=$dir/running.txt"; return 0
  elif [[ $role == source ]]; then
    complete_session "$token" || return
    say "SOURCE_RESUME_PASS=$token"; say SOURCE_RECOVERED
  else
    printf '%s\n' services-ready > "$dir/phase"
    say "TARGET_SERVICES_READY=$token TOKEN_RETAINED_UNTIL_TARGET_COMPLETE"
  fi
  say BUSINESS_ACCEPTANCE_PENDING
}
source_resume_if_active() {
  local token=$1
  validate_token "$token" || return
  if [[ ! -e $STATE_ROOT/active ]]; then say "SOURCE_POST_RESUME_SKIPPED_NO_ACTIVE_SESSION=$token"; return 0; fi
  check_active "$token" source || return
  # An early Jenkins fallback while a copy still owns its lease must not
  # consume the owner's later one-shot compensation attempt.
  transfer_absent "$(session_dir "$token")" || return
  if [[ -f $(session_dir "$token")/phase && $(cat "$(session_dir "$token")/phase") == profile-recovered ]]; then
    fail 'explicit minimal source profile is active; full-original state retained for manual source-resume'; return
  fi
  if [[ -f $(session_dir "$token")/automatic-resume-attempted ]]; then
    fail 'automatic source recovery already attempted; retained token requires explicit source-resume'; return
  fi
  : > "$(session_dir "$token")/automatic-resume-attempted"
  resume source "$token"
}
target_requiesce() {
  local token=$1 dir current
  check_active "$token" target || return
  dir=$(session_dir "$token"); current="$dir/requiesce.monit"
  monit_snapshot "$current" || { fail 'cannot inventory target before compensation'; return; }
  list_all "$current" > "$dir/requiesce-all.txt"
  stop_inventory "$dir/requiesce-all.txt" || return 3
  wait_cold || return
  printf '%s\n' requiesced > "$dir/phase"
  say "TARGET_REQUIESCED=$token"
}
target_complete() {
  local token=$1 dir
  check_active "$token" target || return
  dir=$(session_dir "$token")
  [[ -f $dir/desired.txt ]] && state_matches "$dir/desired.txt" "$dir/all.txt" "$dir/complete.monit" || { fail 'target desired state not verified'; return; }
  xrocket_verify "$dir/desired.txt" || return
  complete_session "$token" || return
  say "TARGET_LIFECYCLE_COMPLETE=$token BUSINESS_ACCEPTANCE_PENDING"
}
target_check() {
  if [[ $# == 1 ]]; then check_session_cold target "$1"
  else check_single_node && require_monit || return; say "TARGET_HOST=$(hostname_proc) READ_ONLY=YES"; monit_call summary; fi
}
target_start() {
  fail 'legacy unbounded target-start disabled; use target-freeze TOKEN --yes then target-resume TOKEN [--desired-file FILE]'
}
usage() {
  cat <<'USAGE'
Green V1.1 local lifecycle controller:
  source-freeze TOKEN --yes | source-check TOKEN
  source-resume TOKEN [--desired-file FILE] | source-resume-if-active TOKEN
  source-transfer-begin TOKEN PID | source-transfer-end TOKEN PID
  target-freeze TOKEN --yes | target-check [TOKEN]
  target-resume TOKEN [--desired-file FILE] | target-requiesce TOKEN
  target-complete TOKEN | cold-check
No operation removes pidfiles or starts/stops all supervisor services in bulk.
Active or unresolved tokens must be reconciled, never silently discarded.
USAGE
}
main() {
  local op=${1:-help}; (($#==0)) || shift
  case "$op" in help|-h|--help) usage; return;; esac
  root_only && validate_waits && need pgrep && need awk && need timeout || return
  # Quiet probes are independent readers, using unique temporary snapshots.
  # The background copy watcher and foreground verification must not contend
  # for the mutator lock and invalidate a healthy image. They never start/stop
  # a process or alter lifecycle/lease markers.
  if [[ $op == cold-check ]]; then
    [[ $# == 0 ]] || return 2
    ensure_state && require_monit && check_single_node && cold_check
    return
  fi
  acquire_command_lock || return
  case "$op" in
    source-freeze|target-freeze) [[ $# == 1 || $# == 2 ]] || { fail 'freeze TOKEN [--yes]'; return; }; freeze "${op%-freeze}" "$@";;
    source-check) [[ $# == 1 ]] || return 2; check_session_cold source "$1";;
    source-resume|target-resume) [[ $# == 1 || $# == 3 ]] || return 2; resume "${op%-resume}" "$@";;
    source-resume-if-active) [[ $# == 1 ]] || return 2; source_resume_if_active "$1";;
    source-transfer-begin|source-transfer-end) [[ $# == 2 ]] || return 2; "${op//-/_}" "$@";;
    target-check) [[ $# -le 1 ]] || return 2; target_check "$@";;
    target-requiesce) [[ $# == 1 ]] || return 2; target_requiesce "$1";;
    target-complete) [[ $# == 1 ]] || return 2; target_complete "$1";;
    target-start) target_start;;
    *) fail "unknown operation: $op";;
  esac
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
