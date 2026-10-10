#!/usr/bin/env bash
set -euo pipefail
umask 022

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SELF_NAME="green_migration_tool_v27.sh"
EXCLUDE_FILE_DEFAULT="$SCRIPT_DIR/exclude.txt"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
LOG_FILE="$SCRIPT_DIR/green_migration_${TIMESTAMP}.log"

ACTION=""
B_IP=""
B_USER=""
B_PASS=""
RELAY_IP=""
RELAY_USER=""
RELAY_PASS=""
BACKUP_NAME=""
BACKUP_ID=""
LIMIT=""
MODE="auto"
EXCLUDE_FILE="$EXCLUDE_FILE_DEFAULT"

REMOTE_STAGE_ROOT="/opt/data/.xmig_stage"
RELAY_ROOT="/opt/data/green_migration_relay"
SOURCE_ROOT="/"

RABBIT_USER="gj"
RABBIT_PASS="${GREEN_MIG_RABBIT_PASS:-}"
RABBIT_VHOST="/"
RABBIT_TAGS="administrator"

SSH_KEY_FILE="${HOME}/.ssh/id_rsa"

# Jenkins / CI 场景可通过环境变量注入密码，避免把密码硬编码在命令行
GREEN_MIG_B_PASS="${GREEN_MIG_B_PASS:-}"
GREEN_MIG_RELAY_PASS="${GREEN_MIG_RELAY_PASS:-}"
GREEN_MIG_SOURCE_PASS="${GREEN_MIG_SOURCE_PASS:-}"
GREEN_MIG_RABBIT_PASS=""

DEFAULT_ITEMS=(/opt /etc /var /bin /sbin /lib /lib64 /usr)

INCREMENTAL_EXTRA_DIRS=()
OPT_INCREMENTAL_WHITELIST=()
ETC_INCREMENTAL_WHITELIST=()
OPT_INCREMENTAL_EXCLUDES=()

FORCE_OVERWRITE_ITEMS=(
  /etc/profile
  /etc/crontab
  /etc/xbrother_hardinfo
  /etc/xbrother_product
  /var/spool/cron/root
  /var/spool/cron/crontabs/root
)

ITEMS_CSV=""
ITEM_FILE=""

BLOCKED_ITEMS=(
  /dev /proc /sys /run /var/run /tmp
  /etc/mtab /etc/fstab /etc/hostname /etc/hosts /etc/resolv.conf /etc/machine-id
  /etc/localtime /etc/localtime_bak /etc/local.conf
  /var/lib/dbus/machine-id /etc/ssh /etc/network /etc/netplan /etc/sysconfig/network /etc/sysconfig/network-scripts
  /boot
)

LOCAL_HELPER_DIRS=(
  "$SCRIPT_DIR"
  /usr/local/bin
  /usr/bin
  /bin
  /usr/local/sbin
  /usr/sbin
  /sbin
)

TARGET_HELPER_DIRS=(
  "$SCRIPT_DIR"
  /opt/data/green_migration_auto
  /usr/local/bin
  /usr/bin
  /bin
  /usr/local/sbin
  /usr/sbin
  /sbin
)

SSHPASS_BIN=""
RSYNC_BIN=""

ts(){ date '+%Y-%m-%d %H:%M:%S'; }
log_info(){ echo "[$(ts)] [INFO] $*" | tee -a "$LOG_FILE"; }
log_warn(){ echo "[$(ts)] [WARN] $*" | tee -a "$LOG_FILE"; }
log_error(){ echo "[$(ts)] [ERROR] $*" | tee -a "$LOG_FILE"; }

ssh_opts=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=30 -o ServerAliveCountMax=5)

helper_bin_works(){
  local name="$1" bin="$2"
  case "$name" in
    sshpass) "$bin" -V >/dev/null 2>&1 ;;
    rsync) "$bin" --version >/dev/null 2>&1 ;;
    *) "$bin" --help >/dev/null 2>&1 ;;
  esac
}

find_helper_bin_in_dirs(){
  local name="$1"; shift
  local d cand sysbin
  for d in "$@"; do
    cand="$d/$name"
    if [[ -x "$cand" ]] && helper_bin_works "$name" "$cand"; then
      echo "$cand"
      return 0
    fi
  done
  sysbin="$(command -v "$name" 2>/dev/null || true)"
  if [[ -n "$sysbin" ]] && helper_bin_works "$name" "$sysbin"; then
    echo "$sysbin"
    return 0
  fi
  return 1
}

init_helper_bins(){
  SSHPASS_BIN="$(find_helper_bin_in_dirs sshpass "${LOCAL_HELPER_DIRS[@]}" || true)"
  RSYNC_BIN="$(find_helper_bin_in_dirs rsync "${LOCAL_HELPER_DIRS[@]}" || true)"
  log_info "本地 sshpass: ${SSHPASS_BIN:-not found}"
  log_info "本地 rsync: ${RSYNC_BIN:-not found}"
}

have_sshpass(){ [[ -n "${SSHPASS_BIN:-}" ]]; }
have_expect(){ command -v expect >/dev/null 2>&1; }
have_rsync(){ [[ -n "${RSYNC_BIN:-}" ]]; }

target_helper_path_prefix(){
  local joined="" d
  for d in "${TARGET_HELPER_DIRS[@]}"; do
    [[ -n "$joined" ]] && joined+=":"
    joined+="$d"
  done
  printf '%s' "$joined"
}

show_help(){
cat <<EOF
用法：
  直连模式：
    sudo ./$SELF_NAME <action> -b B_IP -u USER [-p PASSWORD] [选项]
  中转模式：
    sudo ./$SELF_NAME <action> --relay-ip IP --relay-user USER [--relay-pass PASSWORD] [选项]

动作：
  直连：precheck | stage | apply
  中转：backup-to-relay | list-relay-backups | precheck-restore | restore-from-relay
  通用：setup-ssh-key-auth

密码注入：
  1) 仍可通过 -p / --relay-pass / --rabbit-pass 显式传入
  2) 也可通过环境变量注入（更适合 Jenkins）：
     GREEN_MIG_B_PASS        直连目标机密码
     GREEN_MIG_RELAY_PASS    中转机密码
     GREEN_MIG_RABBIT_PASS   RabbitMQ 用户密码
EOF
}

parse_args(){
  [[ $# -gt 0 ]] || { show_help; exit 1; }

  case "$1" in
    -h|--help)
      show_help
      exit 0
      ;;
  esac

  ACTION="$1"; shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -h|--help) show_help; exit 0 ;;
      -b) B_IP="$2"; shift 2 ;;
      -u) B_USER="$2"; shift 2 ;;
      -p) B_PASS="$2"; shift 2 ;;
      --relay-ip) RELAY_IP="$2"; shift 2 ;;
      --relay-user) RELAY_USER="$2"; shift 2 ;;
      --relay-pass) RELAY_PASS="$2"; shift 2 ;;
      --relay-root) RELAY_ROOT="$2"; shift 2 ;;
      --backup-name) BACKUP_NAME="$2"; shift 2 ;;
      --backup-id) BACKUP_ID="$2"; shift 2 ;;
      --source-root) SOURCE_ROOT="$2"; shift 2 ;;
      --rabbit-user) RABBIT_USER="$2"; shift 2 ;;
      --rabbit-pass) RABBIT_PASS="$2"; shift 2 ;;
      --rabbit-vhost) RABBIT_VHOST="$2"; shift 2 ;;
      --rabbit-tags) RABBIT_TAGS="$2"; shift 2 ;;
      --ssh-key-file) SSH_KEY_FILE="$2"; shift 2 ;;
      -l) LIMIT="$2"; shift 2 ;;
      --mode) MODE="$2"; shift 2 ;;
      --exclude-file) EXCLUDE_FILE="$2"; shift 2 ;;
      --remote-stage-root) REMOTE_STAGE_ROOT="$2"; shift 2 ;;
      --items) ITEMS_CSV="$2"; shift 2 ;;
      --item-file) ITEM_FILE="$2"; shift 2 ;;
      *) log_error "未知参数：$1"; exit 1 ;;
    esac
  done

  [[ -n "$B_PASS" ]] || B_PASS="$GREEN_MIG_B_PASS"
  [[ -n "$RELAY_PASS" ]] || RELAY_PASS="$GREEN_MIG_RELAY_PASS"
  [[ -n "$RABBIT_PASS" ]] || RABBIT_PASS="$GREEN_MIG_RABBIT_PASS"
}

need_cmd(){ command -v "$1" >/dev/null 2>&1 || { log_error "未检测到 $1"; exit 1; }; }

run_with_expect(){
  local pass="$1"; shift
  local cmd_string
  cmd_string="$(printf "%q " "$@")"
  PASS="$pass" CMD="$cmd_string" expect <<'EOF'
set timeout -1
log_user 0
spawn bash -lc $env(CMD)
expect {
  -re "(?i)are you sure you want to continue connecting" { send "yes\r"; exp_continue }
  -re "(?i)(password|passwd):" { send "$env(PASS)\r"; exp_continue }
  eof
}
catch wait result
set exit_status [lindex $result 3]
exit $exit_status
EOF
}

ssh_cmd(){
  local pass="$1"; shift
  if [[ -n "$pass" ]]; then
    if have_sshpass; then
      "$SSHPASS_BIN" -p "$pass" ssh "${ssh_opts[@]}" "$@"
      return
    elif have_expect; then
      run_with_expect "$pass" ssh "${ssh_opts[@]}" "$@"
      return
    fi
  fi
  ssh "${ssh_opts[@]}" "$@"
}

scp_cmd(){
  local pass="$1"; shift
  if [[ -n "$pass" ]]; then
    if have_sshpass; then
      "$SSHPASS_BIN" -p "$pass" scp "${ssh_opts[@]}" "$@"
      return
    elif have_expect; then
      run_with_expect "$pass" scp "${ssh_opts[@]}" "$@"
      return
    fi
  fi
  scp "${ssh_opts[@]}" "$@"
}

rsync_ssh_cmd(){
  local pass="$1"
  if have_sshpass && [[ -n "$pass" ]]; then
    printf "%q -p %q ssh %s" "$SSHPASS_BIN" "$pass" "${ssh_opts[*]}"
  else
    printf "ssh %s" "${ssh_opts[*]}"
  fi
}

target_rsync_path(){
  local path_prefix
  path_prefix="$(target_helper_path_prefix)"
  printf 'env PATH=%q:$PATH rsync' "$path_prefix"
}

relay_rsync_path(){
  printf 'rsync'
}

target_exec(){ ssh_cmd "$B_PASS" "$B_USER@$B_IP" "$@"; }
target_has_cmd(){ target_exec "export PATH='$(target_helper_path_prefix)':\$PATH; command -v $1 >/dev/null 2>&1"; }
relay_exec(){ ssh_cmd "$RELAY_PASS" "$RELAY_USER@$RELAY_IP" "$@"; }
relay_has_cmd(){ relay_exec "command -v $1 >/dev/null 2>&1"; }

detect_source_tar_exclude_support(){
  local h
  h="$(tar --help 2>&1 || tar 2>&1 || true)"
  echo "$h" | grep -q 'BusyBox' && { echo "X"; return; }
  echo "$h" | grep -q 'File with names to exclude' && { echo "X"; return; }
  echo "$h" | grep -q 'exclude File to exclude' && { echo "exclude"; return; }
  echo "$h" | grep -Eq '(^|[[:space:]])-X([[:space:]]|$)' && { echo "X"; return; }
  echo "$h" | grep -q -- '--exclude' && { echo "exclude"; return; }
  echo "none"
}

detect_rsync_progress_opt(){
  if have_rsync && "$RSYNC_BIN" --help 2>/dev/null | grep -q -- '--info=progress2'; then
    echo "--info=progress2"
  else
    echo "--progress"
  fi
}

choose_direct_mode(){
  case "$MODE" in
    rsync|tar-stream) echo "$MODE" ;;
    auto) if have_rsync && target_has_cmd rsync; then echo "rsync"; else echo "tar-stream"; fi ;;
    *) log_error "不支持的 mode: $MODE"; exit 1 ;;
  esac
}

choose_relay_mode(){
  case "$MODE" in
    rsync|tar-stream) echo "$MODE" ;;
    auto) if have_rsync && relay_has_cmd rsync; then echo "rsync"; else echo "tar-stream"; fi ;;
    *) log_error "不支持的 mode: $MODE"; exit 1 ;;
  esac
}

count_excludes(){ awk 'NF && $0 !~ /^[[:space:]]*#/' "$EXCLUDE_FILE" 2>/dev/null | wc -l | tr -d ' '; }
direct_stage_dir(){ echo "${REMOTE_STAGE_ROOT}/${TIMESTAMP}"; }
relay_backup_dir(){ echo "${RELAY_ROOT}/${BACKUP_NAME}/${TIMESTAMP}"; }
find_relay_resumable_dir(){
  relay_exec "find '${RELAY_ROOT}/${BACKUP_NAME}' -mindepth 1 -maxdepth 1 -type d -exec test -f '{}/.inprogress' ';' -print 2>/dev/null | sort | tail -1" 2>/dev/null || true
}
source_path_exists(){ [[ -e "${SOURCE_ROOT%/}$1" || -L "${SOURCE_ROOT%/}$1" ]]; }

normalize_usr_paths(){
  :
}

resolve_items_to_file(){
  local outfile="$1"
  : > "$outfile"
  if [[ -n "$ITEM_FILE" ]]; then
    [[ -f "$ITEM_FILE" ]] || { log_error "item-file 不存在：$ITEM_FILE"; exit 1; }
    awk 'NF && $0 !~ /^[[:space:]]*#/' "$ITEM_FILE" >> "$outfile"
  elif [[ -n "$ITEMS_CSV" ]]; then
    echo "$ITEMS_CSV" | tr ',' '
' | awk 'NF{print $0}' >> "$outfile"
  else
    local p
    for p in "${DEFAULT_ITEMS[@]}"; do echo "$p" >> "$outfile"; done
  fi

  local checked="${outfile}.checked"
  : > "$checked"
  local p b
  while IFS= read -r p; do
    [[ -n "$p" ]] || continue
    case "$p" in /*) ;; *) log_error "迁移项必须是绝对路径：$p"; exit 1 ;; esac
    for b in "${BLOCKED_ITEMS[@]}"; do
      if [[ "$p" == "$b" || "$p" == "$b/"* ]]; then
        log_error "迁移项命中系统关键路径，禁止迁移：$p"
        exit 1
      fi
    done
    echo "$p" >> "$checked"
  done < "$outfile"
  mv "$checked" "$outfile"
  normalize_usr_paths "$outfile"
}

pre_basic_direct_checks(){
  [[ -n "$B_IP" && -n "$B_USER" ]] || { log_error "缺少直连目标机参数"; exit 1; }
  [[ -f "$EXCLUDE_FILE" ]] || { log_error "排除文件不存在：$EXCLUDE_FILE"; exit 1; }
  [[ -d "$SOURCE_ROOT" ]] || { log_error "source-root 不存在：$SOURCE_ROOT"; exit 1; }
  need_cmd ssh; need_cmd tar
  target_exec "echo ok" >/dev/null 2>&1 || { log_error "无法通过 SSH 登录 $B_USER@$B_IP"; exit 1; }
}

pre_basic_relay_checks(){
  [[ -n "$RELAY_IP" && -n "$RELAY_USER" ]] || { log_error "缺少中转机参数"; exit 1; }
  [[ -f "$EXCLUDE_FILE" ]] || { log_error "排除文件不存在：$EXCLUDE_FILE"; exit 1; }
  [[ -d "$SOURCE_ROOT" ]] || { log_error "source-root 不存在：$SOURCE_ROOT"; exit 1; }
  need_cmd ssh; need_cmd tar
  relay_exec "echo ok" >/dev/null 2>&1 || { log_error "无法通过 SSH 登录中转机 $RELAY_USER@$RELAY_IP"; exit 1; }
}

write_manifest_local(){
  local outfile="$1" mode="$2" items_file="$3"
  cat > "$outfile" <<EOF
timestamp=$TIMESTAMP
source_root=$SOURCE_ROOT
mode=$mode
exclude_file=$(basename "$EXCLUDE_FILE")
rabbit_user=$RABBIT_USER
rabbit_vhost=$RABBIT_VHOST
rabbit_tags=$RABBIT_TAGS
items:
$(cat "$items_file")
EOF
}

generate_owner_manifest_local(){
  local outfile="$1" items_file="$2"
  local p
  : > "$outfile"
  while IFS= read -r p; do
    [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] || continue
    (cd "$SOURCE_ROOT" && find ".${p}" -printf '%p	%u	%g	%U	%G
') >> "$outfile"
  done < "$items_file"
}

ensure_missing_accounts_local(){
  local infile="$1" tmp_accounts uname gname uid gid shell_arg group_arg
  [[ -s "$infile" ]] || return 0

  command -v getent >/dev/null 2>&1 || { log_warn "getent not found, skip missing account creation"; return 0; }
  command -v groupadd >/dev/null 2>&1 || log_warn "groupadd not found, skip missing group creation"
  command -v useradd >/dev/null 2>&1 || log_warn "useradd not found, skip missing user creation"

  tmp_accounts="/tmp/green_mig_owner_accounts_$$.tsv"
  awk -F'\t' 'NF>=5{key=$2 "\t" $3 "\t" $4 "\t" $5; if(!seen[key]++){print key}}' "$infile" > "$tmp_accounts"

  while IFS=$'\t' read -r uname gname uid gid; do
    [[ -n "$gname" && ! "$gname" =~ ^[0-9]+$ && "$gname" =~ ^[A-Za-z_][A-Za-z0-9_.-]*\$?$ ]] || continue
    getent group "$gname" >/dev/null 2>&1 && continue
    command -v groupadd >/dev/null 2>&1 || continue
    if [[ "$gid" =~ ^[0-9]+$ ]] && ! getent group "$gid" >/dev/null 2>&1; then
      groupadd -g "$gid" "$gname" >/dev/null 2>&1 || log_warn "create group failed: $gname gid=$gid"
    else
      groupadd "$gname" >/dev/null 2>&1 || log_warn "create group failed: $gname gid=$gid"
    fi
  done < "$tmp_accounts"

  if [[ -x /sbin/nologin ]]; then
    shell_arg="/sbin/nologin"
  elif [[ -x /usr/sbin/nologin ]]; then
    shell_arg="/usr/sbin/nologin"
  else
    shell_arg="/bin/false"
  fi

  while IFS=$'\t' read -r uname gname uid gid; do
    [[ -n "$uname" && ! "$uname" =~ ^[0-9]+$ && "$uname" =~ ^[A-Za-z_][A-Za-z0-9_.-]*\$?$ ]] || continue
    getent passwd "$uname" >/dev/null 2>&1 && continue
    command -v useradd >/dev/null 2>&1 || continue

    group_arg=""
    if [[ -n "$gname" && ! "$gname" =~ ^[0-9]+$ ]] && getent group "$gname" >/dev/null 2>&1; then
      group_arg="-g $gname"
    fi

    if [[ "$uid" =~ ^[0-9]+$ ]] && ! getent passwd "$uid" >/dev/null 2>&1; then
      useradd -M -s "$shell_arg" -u "$uid" $group_arg "$uname" >/dev/null 2>&1 || log_warn "create user failed: $uname uid=$uid gid=$gid"
    else
      useradd -M -s "$shell_arg" $group_arg "$uname" >/dev/null 2>&1 || log_warn "create user failed: $uname uid=$uid gid=$gid"
    fi
  done < "$tmp_accounts"

  rm -f "$tmp_accounts"
}

apply_owner_manifest_local(){
  local infile="$1"
  local raw_path uname gname uid gid path owner_spec group_spec
  [[ -s "$infile" ]] || { log_warn "owner manifest 不存在或为空，跳过属主属组修正"; return 0; }
  ensure_missing_accounts_local "$infile"
  while IFS=$'	' read -r raw_path uname gname uid gid; do
    [[ -n "$raw_path" ]] || continue
    path="${raw_path#.}"
    [[ -e "$path" || -L "$path" ]] || continue
    owner_spec="$uid"
    group_spec="$gid"
    if [[ -n "$uname" && ! "$uname" =~ ^[0-9]+$ ]] && getent passwd "$uname" >/dev/null 2>&1; then
      owner_spec="$uname"
    fi
    if [[ -n "$gname" && ! "$gname" =~ ^[0-9]+$ ]] && getent group "$gname" >/dev/null 2>&1; then
      group_spec="$gname"
    fi
    if [[ -L "$path" ]]; then
      chown -h "${owner_spec}:${group_spec}" "$path" 2>/dev/null || true
    else
      chown "${owner_spec}:${group_spec}" "$path" 2>/dev/null || true
    fi
  done < "$infile"
}

verify_local_after_restore(){
  [ -e /etc/localtime ] && { [ -d /var/run ] || [ -L /var/run ]; } && [ -e /etc/monitrc ] && [ -d /etc/monit.d ]
}

post_restore_finalize_local(){
  source /etc/profile 2>/dev/null || true
  if command -v rabbitmqctl >/dev/null 2>&1; then
    rabbitmqctl list_users 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$RABBIT_USER" || rabbitmqctl add_user "$RABBIT_USER" "$RABBIT_PASS" >/dev/null 2>&1 || true
    rabbitmqctl set_permissions -p "$RABBIT_VHOST" "$RABBIT_USER" '.*' '.*' '.*' >/dev/null 2>&1 || true
    rabbitmqctl set_user_tags "$RABBIT_USER" $RABBIT_TAGS >/dev/null 2>&1 || true
  fi
  chmod 644 /etc/crontab 2>/dev/null || true
  chmod 644 /etc/profile 2>/dev/null || true
  chmod 600 /var/spool/cron/root 2>/dev/null || true
  chmod 600 /var/spool/cron/crontabs/root 2>/dev/null || true
  if id mysql >/dev/null 2>&1; then
    mkdir -p /var/lib/mysql 2>/dev/null || true
    mkdir -p /var/run/mariadb 2>/dev/null || true
    chown -R mysql:mysql /var/lib/mysql 2>/dev/null || true
    chown -R mysql:mysql /opt/data/mysql 2>/dev/null || true
    chown -R mysql:mysql /var/run/mariadb 2>/dev/null || true
    chmod 755 /var/lib/mysql 2>/dev/null || true
    chmod 755 /opt/data/mysql 2>/dev/null || true
    chmod 755 /var/run/mariadb 2>/dev/null || true
    rm -f /var/lib/mysql/mysql.sock /var/lib/mysql/mysql.sock.lock 2>/dev/null || true
    rm -f /var/run/mariadb/mariadb.pid /opt/data/mysql/mariadb.pid 2>/dev/null || true
  fi
  /etc/init.d/crond restart >/dev/null 2>&1 || /etc/init.d/cron restart >/dev/null 2>&1 || service crond restart >/dev/null 2>&1 || service cron restart >/dev/null 2>&1 || systemctl restart crond >/dev/null 2>&1 || true
  if command -v monit2 >/dev/null 2>&1; then monit2 quit 2>/dev/null || true; fi
  if command -v monit >/dev/null 2>&1; then monit quit 2>/dev/null || true; fi
  pkill -f monit >/dev/null 2>&1 || true
  rm -f /var/run/gonit.pid /var/run/gonit.sock >/dev/null 2>&1 || true
  mkdir -p /var/lib/gonit 2>/dev/null || true
  if command -v monit2 >/dev/null 2>&1; then
    monit2 -c /etc/monitrc -p /var/run/gonit.pid -S /var/run/gonit.sock -s /var/lib/gonit/state -l /opt/log/monit2.log -d 60 >/dev/null 2>&1 || true
  elif command -v monit >/dev/null 2>&1; then
    monit -c /etc/monitrc -p /var/run/gonit.pid -S /var/run/gonit.sock -s /var/lib/gonit/state -l /opt/log/monit.log -d 60 >/dev/null 2>&1 || true
  fi
}

make_opt_incremental_exclude_file(){
  local outfile="$1"
  : > "$outfile"
}


build_whitelist_listfile(){
  local outfile="$1"; shift
  : > "$outfile"
  local p
  for p in "$@"; do
    [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] && echo ".${p}" >> "$outfile"
  done
}

restore_path_whitelist_from_stage(){
  return 0
}


restore_whitelist_from_stage(){
  return 0
}


restore_path_whitelist_from_relay(){
  return 0
}


restore_whitelist_from_relay(){
  return 0
}


force_restore_file_from_relay(){
  local relay_dir="$1" path="$2"
  local rp src dst backup_ts
  rp="${path#/}"
  src="$relay_dir/files/$rp"
  dst="$path"
  backup_ts="$(date +%Y%m%d_%H%M%S)"

  relay_exec "[ -f '$src' ] || [ -L '$src' ]" || return 0

  mkdir -p "$(dirname "$dst")"

  if [[ -e "$dst" || -L "$dst" ]]; then
    cp -a "$dst" "${dst}.bak_${backup_ts}" 2>/dev/null || true
  fi

  if relay_exec "[ -L '$src' ]"; then
    rm -f "$dst"
    local t
    t="$(relay_exec "readlink '$src'")"
    ln -s "$t" "$dst"
  else
    if have_rsync && relay_has_cmd rsync; then
      "$RSYNC_BIN" -aH --links --rsync-path="$(relay_rsync_path)" \
        -e "$(rsync_ssh_cmd "$RELAY_PASS")" \
        "$RELAY_USER@$RELAY_IP:$src" "$dst"
    else
      local src_dir src_base
      src_dir="$(dirname "$src")"
      src_base="$(basename "$src")"
      rm -f "$dst"
      ssh_cmd "$RELAY_PASS" "$RELAY_USER@$RELAY_IP" \
        "cd '$src_dir' && tar cpf - '$src_base'" | tar xpf - -C "$(dirname "$dst")"
    fi
  fi
}

force_restore_items_from_relay(){
  local relay_dir="$1" p
  for p in "${FORCE_OVERWRITE_ITEMS[@]}"; do
    force_restore_file_from_relay "$relay_dir" "$p"
    log_info "已强制覆盖：$p"
  done
}

force_restore_file_from_stage(){
  local stage_dir="$1" path="$2"
  local rp src dst backup_ts
  rp="${path#/}"
  src="$stage_dir/files/$rp"
  dst="$path"
  backup_ts="$(date +%Y%m%d_%H%M%S)"

  target_exec "[ -f '$src' ] || [ -L '$src' ]" || return 0

  target_exec "mkdir -p '$(dirname "$dst")'"

  if target_exec "[ -e '$dst' ] || [ -L '$dst' ]"; then
    target_exec "cp -a '$dst' '${dst}.bak_${backup_ts}' 2>/dev/null || true"
  fi

  target_exec "
    if [ -L '$src' ]; then
      rm -f '$dst'
      ln -s \"\$(readlink '$src')\" '$dst'
    elif [ -f '$src' ]; then
      cp -af '$src' '$dst'
    fi
  "
}

force_restore_items_from_stage(){
  local stage_dir="$1" p
  for p in "${FORCE_OVERWRITE_ITEMS[@]}"; do
    force_restore_file_from_stage "$stage_dir" "$p"
    log_info "已强制覆盖：$p"
  done
}

relay_restore_dir_nonoverwrite(){
  local src="$1" dst="$2"
  mkdir -p "$dst"
  find "$dst" -xtype l -delete 2>/dev/null || true
  if have_rsync && relay_has_cmd rsync; then
    "$RSYNC_BIN" -aH --links --ignore-existing --rsync-path="$(relay_rsync_path)" -e "$(rsync_ssh_cmd "$RELAY_PASS")" "$RELAY_USER@$RELAY_IP:$src/" "$dst/"
  else
    ssh_cmd "$RELAY_PASS" "$RELAY_USER@$RELAY_IP" "cd '$src' && tar cpf - ." | tar xpf - -C "$dst"
  fi
}

relay_restore_file_nonoverwrite(){
  local src="$1" dst="$2"
  local dst_dir src_dir src_base
  dst_dir="$(dirname "$dst")"
  mkdir -p "$dst_dir"
  if [[ -L "$dst" && ! -e "$dst" ]]; then
    rm -f "$dst"
  fi
  [[ -e "$dst" || -L "$dst" ]] && return 0
  if relay_exec "[ -L '$src' ]"; then
    local t
    t="$(relay_exec "readlink '$src'")"
    ln -s "$t" "$dst"
  else
    if have_rsync && relay_has_cmd rsync; then
      "$RSYNC_BIN" -aH --links --ignore-existing --rsync-path="$(relay_rsync_path)" -e "$(rsync_ssh_cmd "$RELAY_PASS")" "$RELAY_USER@$RELAY_IP:$src" "$dst"
    else
      src_dir="$(dirname "$src")"
      src_base="$(basename "$src")"
      ssh_cmd "$RELAY_PASS" "$RELAY_USER@$RELAY_IP" "cd '$src_dir' && tar cpf - '$src_base'" | tar xpf - -C "$dst_dir"
    fi
  fi
}

ensure_local_ssh_key(){
  local key_dir pub_file
  key_dir="$(dirname "$SSH_KEY_FILE")"
  pub_file="${SSH_KEY_FILE}.pub"
  mkdir -p "$key_dir"
  chmod 700 "$key_dir"
  if [[ ! -f "$SSH_KEY_FILE" || ! -f "$pub_file" ]]; then
    need_cmd ssh-keygen
    ssh-keygen -t rsa -N '' -f "$SSH_KEY_FILE" >/dev/null
    log_info "已生成本机 SSH 密钥：$SSH_KEY_FILE"
  else
    log_info "检测到已有本机 SSH 密钥：$SSH_KEY_FILE"
  fi
}

setup_remote_ssh_key_auth(){
  local remote_user="$1" remote_host="$2" remote_pass="$3" pub_file tmp_remote
  pub_file="${SSH_KEY_FILE}.pub"
  tmp_remote="/tmp/green_mig_pub_$$.pub"
  ensure_local_ssh_key
  if [[ -n "$remote_pass" ]] && ! have_sshpass && ! have_expect; then
    log_warn "当前机器未安装 sshpass/expect，若远端尚未建立信任关系，setup-ssh-key-auth 可能会提示手工输入密码。"
  fi
  ssh_cmd "$remote_pass" "$remote_user@$remote_host" "mkdir -p ~/.ssh && chmod 700 ~/.ssh && touch ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
  scp_cmd "$remote_pass" "$pub_file" "$remote_user@$remote_host:$tmp_remote"
  ssh_cmd "$remote_pass" "$remote_user@$remote_host" "cat '$tmp_remote' >> ~/.ssh/authorized_keys; awk 'NF && !seen[\$0]++' ~/.ssh/authorized_keys > ~/.ssh/authorized_keys.tmp && mv ~/.ssh/authorized_keys.tmp ~/.ssh/authorized_keys; sed -i 's/\r\$//' ~/.ssh/authorized_keys 2>/dev/null || true; rm -f '$tmp_remote'; chmod 700 ~/.ssh; chmod 600 ~/.ssh/authorized_keys"
  ssh -i "$SSH_KEY_FILE" -o IdentitiesOnly=yes -o BatchMode=yes "${ssh_opts[@]}" "$remote_user@$remote_host" "echo ok" >/dev/null 2>&1 || { log_error "SSH 免密验证失败：$remote_user@$remote_host"; exit 1; }
  log_info "SSH 免密配置成功：$remote_user@$remote_host"
}

setup_ssh_key_auth_action(){
  need_cmd ssh
  if [[ -n "$B_IP" && -n "$B_USER" ]]; then
    log_info "开始配置直连目标机 SSH 免密：$B_USER@$B_IP"
    setup_remote_ssh_key_auth "$B_USER" "$B_IP" "$B_PASS"
    return
  fi
  if [[ -n "$RELAY_IP" && -n "$RELAY_USER" ]]; then
    log_info "开始配置中转机 SSH 免密：$RELAY_USER@$RELAY_IP"
    setup_remote_ssh_key_auth "$RELAY_USER" "$RELAY_IP" "$RELAY_PASS"
    return
  fi
  log_error "setup-ssh-key-auth 需要直连参数(-b -u [-p])或中转参数(--relay-ip --relay-user [--relay-pass])"
  exit 1
}

stage_extra_incremental_rsync(){
  return 0
}


stage_extra_incremental_tar(){
  return 0
}


backup_extra_incremental_rsync(){
  return 0
}


backup_extra_incremental_tar(){
  return 0
}


restore_extra_incremental_from_stage(){
  return 0
}


restore_extra_incremental_from_relay(){
  return 0
}


precheck_action(){
  pre_basic_direct_checks
  local items_file="/tmp/green_mig_items_precheck_$$.txt"
  resolve_items_to_file "$items_file"
  log_info "开始 precheck..."
  log_info "源根目录：$SOURCE_ROOT"
  log_info "目标 stage 根目录：$REMOTE_STAGE_ROOT"
  log_info "排除项数量：$(count_excludes)"
  log_info "建议迁移模式：$(choose_direct_mode)"
  log_info "tar 排除能力：$(detect_source_tar_exclude_support)"
  log_info "增量补缺目录：${INCREMENTAL_EXTRA_DIRS[*]}"
  log_info "增量补缺 /opt 白名单：${OPT_INCREMENTAL_WHITELIST[*]}"
  log_info "增量补缺 /etc 白名单：${ETC_INCREMENTAL_WHITELIST[*]}"
  log_info "precheck 完成。"
}

direct_stage_rsync(){
  local stage_dir="$1" items_file="$2"
  local progress_opt rsync_opts p rp
  local -a stage_items=()
  progress_opt="$(detect_rsync_progress_opt)"
  rsync_opts=(-aH --numeric-ids --links --partial --human-readable "$progress_opt" --log-file="$LOG_FILE")
  [[ -n "$LIMIT" ]] && rsync_opts+=(--bwlimit="$LIMIT")
  mapfile -t stage_items < "$items_file"
  for p in "${stage_items[@]}"; do
    [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] || continue
    rp="${p#/}"
    target_exec "mkdir -p '$stage_dir/files/$(dirname "$rp")'" >/dev/null 2>&1 || true
    "$RSYNC_BIN" "${rsync_opts[@]}" --rsync-path="$(target_rsync_path)" -e "$(rsync_ssh_cmd "$B_PASS")" "${SOURCE_ROOT%/}${p}" "$B_USER@$B_IP:$stage_dir/files/$(dirname "$rp")/"
  done
  stage_extra_incremental_rsync "$stage_dir"
}

direct_stage_tar(){
  local stage_dir="$1" items_file="$2"
  local tar_mode listfile p
  tar_mode="$(detect_source_tar_exclude_support)"
  [[ "$tar_mode" == "none" ]] && { log_error "源端 tar 不支持排除清单"; return 2; }
  target_exec "mkdir -p '$stage_dir/files'" >/dev/null
  listfile="/tmp/green_mig_tar_items_$$.txt"; : > "$listfile"
  while IFS= read -r p; do [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] && echo ".${p}" >> "$listfile"; done < "$items_file"
  if [[ "$tar_mode" == "exclude" ]]; then
    (cd "$SOURCE_ROOT" && tar cpf - -T "$listfile" --exclude-from="$EXCLUDE_FILE") | target_exec "tar xpf - -C '$stage_dir/files'"
  else
    (cd "$SOURCE_ROOT" && tar cpf - -T "$listfile" -X "$EXCLUDE_FILE") | target_exec "tar xpf - -C '$stage_dir/files'"
  fi
  stage_extra_incremental_tar "$stage_dir"
}

stage_action(){
  pre_basic_direct_checks
  local items_file="/tmp/green_mig_items_stage_$$.txt"
  local stage_dir selected_mode manifest="/tmp/green_mig_manifest_$$.txt"
  resolve_items_to_file "$items_file"
  stage_dir="$(direct_stage_dir)"
  selected_mode="$(choose_direct_mode)"
  target_exec "mkdir -p '$stage_dir/files'" >/dev/null
  if [[ "$selected_mode" == "rsync" ]]; then
    direct_stage_rsync "$stage_dir" "$items_file"
  else
    direct_stage_tar "$stage_dir" "$items_file"
  fi
  write_manifest_local "$manifest" "$selected_mode" "$items_file"
  scp_cmd "$B_PASS" "$manifest" "$B_USER@$B_IP:$stage_dir/manifest.txt" >/dev/null 2>&1
  log_info "stage 完成。请到目标机查看：$stage_dir"
}

apply_action(){
  pre_basic_direct_checks
  local stage_dir items_tmp
  stage_dir="$(target_exec "ls -1dt '${REMOTE_STAGE_ROOT}'/* 2>/dev/null | head -1")"
  [[ -n "${stage_dir:-}" ]] || { log_error "未找到可用 stage 目录"; exit 1; }
  items_tmp="/tmp/green_mig_apply_items_$$.txt"
  target_exec "awk '/^items:/{flag=1;next} flag{print}' '$stage_dir/manifest.txt' 2>/dev/null" > "$items_tmp"

  local p rp target source_item remote_cmd
  local -a apply_items=()
  mapfile -t apply_items < "$items_tmp"
  for p in "${apply_items[@]}"; do
    [[ -n "$p" ]] || continue
    rp="${p#/}"
    target="/$rp"
    source_item="$stage_dir/files/$rp"
    remote_cmd=$(cat <<EOF
if [ -d '$source_item' ]; then
  mkdir -p '$target'
  find '$target' -xtype l -delete 2>/dev/null || true
  if command -v rsync >/dev/null 2>&1; then
    if [ '$target' = '/opt/data' ]; then
      rsync -aH --links --ignore-existing --exclude='.xmig_stage' --exclude='.xmig_backup' --exclude='green_migration_relay' '$source_item/' '$target/' >/dev/null 2>&1 || exit 11
    else
      rsync -aH --links --ignore-existing '$source_item/' '$target/' >/dev/null 2>&1 || exit 11
    fi
  else
    cp -an '$source_item/.' '$target/' >/dev/null 2>&1 || exit 12
  fi
elif [ -f '$source_item' ] || [ -L '$source_item' ]; then
  mkdir -p "$(dirname "$target")"
  if [ -L '$target' ] && [ ! -e '$target' ]; then
    rm -f '$target'
  fi
  if [ ! -e '$target' ] && [ ! -L '$target' ]; then
    cp -a '$source_item' '$target' >/dev/null 2>&1 || exit 13
  fi
fi
EOF
)
    target_exec "$remote_cmd" || { log_error "落地失败：$target"; exit 1; }
  done

  restore_extra_incremental_from_stage "$stage_dir"
  force_restore_items_from_stage "$stage_dir"

  if target_exec "[ -e /etc/localtime ] && ( [ -d /var/run ] || [ -L /var/run ] ) && [ -e /etc/monitrc ] && [ -d /etc/monit.d ]"; then
    target_exec "$(typeset -f post_restore_finalize_local); post_restore_finalize_local"
  else
    log_warn "基础验证未通过，跳过收尾动作"
  fi
  log_info "apply 完成。"
}

backup_to_relay_rsync(){
  local relay_dir="$1" items_file="$2"
  local progress_opt rsync_opts p rp
  local -a backup_items=()
  progress_opt="$(detect_rsync_progress_opt)"
  rsync_opts=(-aH --numeric-ids --links --partial --human-readable "$progress_opt" --log-file="$LOG_FILE")
  [[ -n "$LIMIT" ]] && rsync_opts+=(--bwlimit="$LIMIT")
  mapfile -t backup_items < "$items_file"
  for p in "${backup_items[@]}"; do
    [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] || continue
    rp="${p#/}"
    relay_exec "mkdir -p '$relay_dir/files/$(dirname "$rp")'" >/dev/null 2>&1 || true
    "$RSYNC_BIN" "${rsync_opts[@]}" --rsync-path="$(relay_rsync_path)" -e "$(rsync_ssh_cmd "$RELAY_PASS")" "${SOURCE_ROOT%/}${p}" "$RELAY_USER@$RELAY_IP:$relay_dir/files/$(dirname "$rp")/"
  done
  backup_extra_incremental_rsync "$relay_dir"
}

backup_to_relay_tar(){
  local relay_dir="$1" items_file="$2"
  local tar_mode listfile p
  local -a backup_items=()
  tar_mode="$(detect_source_tar_exclude_support)"
  [[ "$tar_mode" == "none" ]] && { log_error "源端 tar 不支持排除清单"; return 2; }
  relay_exec "mkdir -p '$relay_dir/files'" >/dev/null
  listfile="/tmp/green_mig_relay_tar_items_$$.txt"; : > "$listfile"
  mapfile -t backup_items < "$items_file"
  for p in "${backup_items[@]}"; do
    [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] && echo ".${p}" >> "$listfile"
  done
  if [[ "$tar_mode" == "exclude" ]]; then
    (cd "$SOURCE_ROOT" && tar cpf - -T "$listfile" --exclude-from="$EXCLUDE_FILE") | relay_exec "tar xpf - -C '$relay_dir/files'"
  else
    (cd "$SOURCE_ROOT" && tar cpf - -T "$listfile" -X "$EXCLUDE_FILE") | relay_exec "tar xpf - -C '$relay_dir/files'"
  fi
  backup_extra_incremental_tar "$relay_dir"
}

backup_to_relay_action(){
  pre_basic_relay_checks
  [[ -n "$BACKUP_NAME" ]] || { log_error "backup-to-relay 需要 --backup-name"; exit 1; }
  local items_file="/tmp/green_mig_items_relay_$$.txt"
  local manifest="/tmp/green_mig_manifest_relay_$$.txt"
  local owner_manifest="/tmp/green_mig_owner_manifest_relay_$$.tsv"
  local relay_dir selected_mode resume_dir backup_id
  resolve_items_to_file "$items_file"
  selected_mode="$(choose_relay_mode)"

  if [[ "$selected_mode" == "rsync" ]]; then
    resume_dir="$(find_relay_resumable_dir)"
    if [[ -n "$resume_dir" ]]; then
      relay_dir="$resume_dir"
      backup_id="${BACKUP_NAME}/$(basename "$relay_dir")"
      log_warn "检测到未完成备份，继续续传：$backup_id"
    else
      relay_dir="$(relay_backup_dir)"
      backup_id="${BACKUP_NAME}/${TIMESTAMP}"
    fi
  else
    relay_dir="$(relay_backup_dir)"
    backup_id="${BACKUP_NAME}/${TIMESTAMP}"
  fi

  relay_exec "mkdir -p '$relay_dir/files' && rm -f '$relay_dir/manifest.txt' '$relay_dir/owners.tsv' && touch '$relay_dir/.inprogress'" >/dev/null

  if [[ "$selected_mode" == "rsync" ]]; then
    backup_to_relay_rsync "$relay_dir" "$items_file"
  else
    backup_to_relay_tar "$relay_dir" "$items_file"
  fi

  write_manifest_local "$manifest" "$selected_mode" "$items_file"
  generate_owner_manifest_local "$owner_manifest" "$items_file"
  scp_cmd "$RELAY_PASS" "$manifest" "$RELAY_USER@$RELAY_IP:$relay_dir/manifest.txt" >/dev/null 2>&1
  scp_cmd "$RELAY_PASS" "$owner_manifest" "$RELAY_USER@$RELAY_IP:$relay_dir/owners.tsv" >/dev/null 2>&1
  relay_exec "rm -f '$relay_dir/.inprogress'" >/dev/null 2>&1 || true
  log_info "backup-to-relay 完成。备份 ID：$backup_id"
}

list_relay_backups_action(){
  pre_basic_relay_checks
  relay_exec "find '$RELAY_ROOT' -mindepth 2 -maxdepth 2 -type d 2>/dev/null | sed 's#^$RELAY_ROOT/##' | sort"
}

precheck_restore_action(){
  pre_basic_relay_checks
  [[ -n "$BACKUP_ID" ]] || { log_error "precheck-restore 需要 --backup-id"; exit 1; }
  relay_exec "[ -f '$RELAY_ROOT/$BACKUP_ID/manifest.txt' ]" || { log_error "中转机上未找到该备份：$BACKUP_ID"; exit 1; }
  log_info "precheck-restore 完成。"
}

restore_from_relay_action(){
  pre_basic_relay_checks
  [[ -n "$BACKUP_ID" ]] || { log_error "restore-from-relay 需要 --backup-id"; exit 1; }
  local relay_dir="$RELAY_ROOT/$BACKUP_ID"
  relay_exec "[ -d '$relay_dir/files' ]" || { log_error "中转机上未找到该备份内容：$BACKUP_ID"; exit 1; }
  local items_file="/tmp/green_mig_restore_items_$$.txt"
  local owner_manifest="/tmp/green_mig_restore_owner_$$.tsv"
  relay_exec "awk '/^items:/{flag=1;next} flag{print}' '$relay_dir/manifest.txt'" > "$items_file"
  relay_exec "[ -f '$relay_dir/owners.tsv' ] && cat '$relay_dir/owners.tsv'" > "$owner_manifest" 2>/dev/null || true
  [[ -s "$items_file" ]] || { log_error "备份 manifest 中未找到 items 列表"; exit 1; }

  log_info "开始 restore-from-relay（直拉恢复，不落本地大 stage）..."
  local p rp target src
  local -a restore_items=()
  mapfile -t restore_items < "$items_file"
  for p in "${restore_items[@]}"; do
    [[ -n "$p" ]] || continue
    rp="${p#/}"; target="/$rp"; src="$relay_dir/files/$rp"
    if relay_exec "[ -d '$src' ]"; then
      mkdir -p "$target"
      if [[ "$target" == "/opt/data" ]]; then
        if have_rsync && relay_has_cmd rsync; then
          "$RSYNC_BIN" -aH --links --ignore-existing --exclude='.xmig_stage' --exclude='.xmig_backup' --exclude='green_migration_relay' --rsync-path="$(relay_rsync_path)" -e "$(rsync_ssh_cmd "$RELAY_PASS")" "$RELAY_USER@$RELAY_IP:$src/" "$target/"
        else
          ssh_cmd "$RELAY_PASS" "$RELAY_USER@$RELAY_IP" "cd '$src' && tar cpf - --exclude='.xmig_stage' --exclude='.xmig_backup' --exclude='green_migration_relay' ." | tar xpf - -C "$target"
        fi
      else
        relay_restore_dir_nonoverwrite "$src" "$target"
      fi
      log_info "已恢复目录：$target"
    else
      relay_restore_file_nonoverwrite "$src" "$target"
      log_info "已恢复文件/链接：$target"
    fi
  done

  restore_extra_incremental_from_relay "$relay_dir"
  force_restore_items_from_relay "$relay_dir"
  apply_owner_manifest_local "$owner_manifest"
  log_info "已按 owner manifest 修正属主属组"

  if verify_local_after_restore; then
    log_info "基础验证通过。收尾动作已移出脚本，请在 Jenkins 独立 stage 执行。"
  else