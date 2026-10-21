#!/usr/bin/env bash
# Parameter/transport adapter only. Service control and image transactions are
# owned by green_v1_services.sh and green_migration_tool.sh respectively.
set +x
set -Eeuo pipefail
umask 077
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

die() { printf 'JENKINS_BLOCKED=%s\n' "$*" >&2; exit 2; }
say() { printf '%s\n' "$*"; }
required() { [[ -n ${!1:-} ]] || die "missing parameter $1"; }
boolean() { [[ ${!1:-false} == true || ${!1:-false} == false ]] || die "invalid boolean $1"; }
safe_node() { [[ $1 =~ ^[A-Za-z0-9][A-Za-z0-9_.:-]*$ && $1 != -* ]] || die 'unsafe host'; }
safe_user() { [[ $1 =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || die 'unsafe SSH user'; }
safe_path() {
  [[ $1 =~ ^/[A-Za-z0-9_./-]+$ && $1 != / && $1 != *'//'* && /${1#/}/ != *'/../'* && /${1#/}/ != *'/./'* ]] || die 'unsafe absolute path';
}
safe_id() { [[ $1 =~ ^[A-Za-z0-9_-]+/[0-9]{8}_[0-9]{6}$ ]] || die 'unsafe BACKUP_ID'; }
session_token() {
  local token="${SESSION_TOKEN:-}"
  if [[ -z $token ]]; then
    required BUILD_NUMBER
    [[ $BUILD_NUMBER =~ ^[0-9]+$ ]] || die 'invalid BUILD_NUMBER';
    token="j-$(printf '%s' "${JOB_NAME:-green-migration-test}" | sha256sum | cut -c1-16)-$BUILD_NUMBER"
  fi
  [[ $token =~ ^[A-Za-z0-9_.-]{1,64}$ ]] || die 'invalid SESSION_TOKEN'
  printf '%s' "$token"
}
scratch_init() {
  required GREEN_MIG_SCRATCH
  [[ $GREEN_MIG_SCRATCH == /* && ! -L $GREEN_MIG_SCRATCH ]] || die 'unsafe scratch directory'
  mkdir -p -- "$GREEN_MIG_SCRATCH"
  [[ ! -L $GREEN_MIG_SCRATCH ]] || die 'scratch symlink'
  [[ ! -L $GREEN_MIG_SCRATCH/session ]] || die 'scratch session symlink'
  if [[ ! -e $GREEN_MIG_SCRATCH/session ]]; then
    (set -o noclobber; printf '%s\n' "$GREEN_V11_SESSION" > "$GREEN_MIG_SCRATCH/session") 2>/dev/null || true
  fi
  [[ -f $GREEN_MIG_SCRATCH/session && $(cat "$GREEN_MIG_SCRATCH/session") == "$GREEN_V11_SESSION" ]] || die 'scratch belongs to another session'
}
defaults() {
  ACTION="${ACTION:-}"
  TRANSFER_MODE="${TRANSFER_MODE:-relay}"
  SOURCE_ROOT="${SOURCE_ROOT:-/}"
  RELAY_ROOT="${RELAY_ROOT:-/opt/data/green_migration_relay}"
  REMOTE_DIR_SOURCE="${REMOTE_DIR_SOURCE:-/opt/data/green_migration_auto}"
  REMOTE_DIR_TARGET="${REMOTE_DIR_TARGET:-/opt/data/green_migration_auto}"
  REMOTE_DIR_RELAY="${REMOTE_DIR_RELAY:-/opt/data/green_migration_auto}"
  IMAGE_STORE="${IMAGE_STORE:-local}"
  IMAGE_ARCHIVE_ROOT="${IMAGE_ARCHIVE_ROOT:-}"
  if [[ -z $IMAGE_ARCHIVE_ROOT ]]; then
    if [[ $IMAGE_STORE == webdav ]]; then IMAGE_ARCHIVE_ROOT=/opt/data/webdav/Base/auguImages
    else IMAGE_ARCHIVE_ROOT=/opt/data/green-migration-images; fi
  fi
  KNOWN_HOSTS_FILE="${KNOWN_HOSTS_FILE:-${HOME}/.ssh/known_hosts}"
  GREEN_V11_SESSION="$(session_token)"
  GREEN_V11_RELAY_SCRIPT_DIR="${REMOTE_DIR_RELAY%/}/v1.1-$GREEN_V11_SESSION"
  GREEN_V11_TARGET_SCRIPT_DIR="${REMOTE_DIR_TARGET%/}/v1.1-$GREEN_V11_SESSION"
  export GREEN_V11_RELAY_SCRIPT_DIR GREEN_V11_TARGET_SCRIPT_DIR
  ssh_options=(-o StrictHostKeyChecking=yes -o "UserKnownHostsFile=$KNOWN_HOSTS_FILE" -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)
}
validate() {
  case $ACTION in precheck|stage|apply|backup-to-relay|list-relay-backups|list-archived-images|precheck-restore|restore-from-relay|setup-ssh-key-auth) ;; *) die 'unsupported ACTION';; esac
  [[ $TRANSFER_MODE == relay || $TRANSFER_MODE == direct ]] || die 'invalid TRANSFER_MODE'
  [[ $IMAGE_STORE == none || $IMAGE_STORE == local || $IMAGE_STORE == webdav ]] || die 'invalid IMAGE_STORE'
  [[ ${RESTORE_MODE:-merge-preserve} == merge-preserve || ${RESTORE_MODE:-merge-preserve} == replace-managed ]] || die 'invalid RESTORE_MODE'
  [[ ${TARGET_MODE:-managed} == managed || ${TARGET_MODE:-managed} == offline || ${TARGET_MODE:-managed} == fixture ]] || die 'invalid TARGET_MODE'
  for name in DEBUG_LOGS SOURCE_SERVICE_CYCLE TARGET_SERVICE_CYCLE IMAGE_DESIRED_SERVICES; do boolean "$name"; done
  for name in REMOTE_DIR_SOURCE REMOTE_DIR_TARGET REMOTE_DIR_RELAY RELAY_ROOT IMAGE_ARCHIVE_ROOT; do safe_path "${!name}"; done
  [[ $SOURCE_ROOT == / ]] || safe_path "$SOURCE_ROOT"
  [[ ${TARGET_ROOT:-/} == / ]] || safe_path "$TARGET_ROOT"
  if [[ $TRANSFER_MODE == direct && ${TARGET_ENV:-} == rainbowC ]]; then die 'legacy direct rainbowC is unsupported'; fi
  case $ACTION in
    backup-to-relay|stage) required SOURCE_IP; required SOURCE_USER; safe_node "$SOURCE_IP"; safe_user "$SOURCE_USER" ;;
  esac
  case $ACTION in
    backup-to-relay|list-relay-backups|list-archived-images|precheck-restore|restore-from-relay|setup-ssh-key-auth)
      if [[ $TRANSFER_MODE == relay || $ACTION != setup-ssh-key-auth ]]; then required RELAY_IP; required RELAY_USER; safe_node "$RELAY_IP"; safe_user "$RELAY_USER"; fi ;;
  esac
  case $ACTION in
    precheck|stage|apply|precheck-restore|restore-from-relay|setup-ssh-key-auth) required TARGET_IP; required TARGET_USER; safe_node "$TARGET_IP"; safe_user "$TARGET_USER" ;;
  esac
  if [[ ${SOURCE_SERVICE_CYCLE:-false} == true ]]; then
    [[ $ACTION == backup-to-relay && $SOURCE_ROOT == / ]] || die 'SOURCE_SERVICE_CYCLE requires backup-to-relay of /'
    [[ $SOURCE_IP != "$RELAY_IP" ]] || die 'source and relay must differ for source lifecycle'
  fi
  if [[ $ACTION == backup-to-relay ]]; then
    required BACKUP_NAME
    [[ $BACKUP_NAME =~ ^[A-Za-z0-9_-]+$ ]] || die 'unsafe BACKUP_NAME'
  fi
  if [[ $ACTION == restore-from-relay || $ACTION == precheck-restore || $ACTION == apply ]]; then
    if [[ $ACTION == apply ]]; then
      [[ ${TARGET_STAGE_ID:-${STAGE_ID:-}} =~ ^[0-9]{8}_[0-9]{6}$ ]] || die 'explicit TARGET_STAGE_ID required'
    else
      required BACKUP_ID; safe_id "$BACKUP_ID"
    fi
    required TARGET_NODE_ID; required EXPECT_SOURCE_ID
    [[ -z ${SOURCE_IP:-} || $SOURCE_IP != "$TARGET_IP" ]] || die 'source and target must differ'
    [[ -z ${RELAY_IP:-} || $TARGET_IP != "$RELAY_IP" ]] || die 'target and relay must differ'
    if [[ -n ${RESTORE_ALLOWLIST:-} ]]; then [[ -f $RESTORE_ALLOWLIST && ! -L $RESTORE_ALLOWLIST ]] || die 'RESTORE_ALLOWLIST unavailable'; fi
  fi
  if [[ -n ${ITEM_FILE:-} ]]; then [[ -f $ITEM_FILE && ! -L $ITEM_FILE ]] || die 'ITEM_FILE unavailable'; fi
  if [[ -n ${SOURCE_DESIRED_SERVICES_FILE:-} ]]; then [[ -f $SOURCE_DESIRED_SERVICES_FILE && ! -L $SOURCE_DESIRED_SERVICES_FILE ]] || die 'SOURCE_DESIRED_SERVICES_FILE unavailable'; fi
  [[ -f $KNOWN_HOSTS_FILE && -s $KNOWN_HOSTS_FILE ]] || die 'reviewed KNOWN_HOSTS_FILE required'
  [[ ${SSH_COMMAND_TIMEOUT:-7200} =~ ^[1-9][0-9]*$ ]] || die 'invalid SSH_COMMAND_TIMEOUT'
  for file in green_migration_tool.sh green_v1_services.sh green_v11_image.py green_v1_target_start.sh exclude.txt items_rootpkg.txt; do
    [[ -f $SCRIPT_DIR/$file && ! -L $SCRIPT_DIR/$file ]] || die "release file missing: $file"
  done
  bash -n "$SCRIPT_DIR/green_migration_tool.sh"
  bash -n "$SCRIPT_DIR/green_v1_services.sh"
  bash -n "$SCRIPT_DIR/green_v1_target_start.sh"
  say 'JENKINS_PARAMETERS=PASS; LIVE_CONFIGURATION=UNVERIFIED'
}
transport() {
  local password="$1"; shift
  if [[ -n $password ]]; then
    SSHPASS="$password" timeout --signal=TERM --kill-after=15 "${SSH_COMMAND_TIMEOUT:-7200}" sshpass -e "$@"
  else
    timeout --signal=TERM --kill-after=15 "${SSH_COMMAND_TIMEOUT:-7200}" "$@"
  fi
}
remote_script() {
  local host="$1" user="$2" password="$3"; shift 3
  transport "$password" ssh "${ssh_options[@]}" -T -- "$user@$host" 'bash --noprofile --norc -s'
}
bootstrap() {
  local dir="$1" program="$2"; shift 2
  printf '%s\n' 'set +x' 'set -Eeuo pipefail' 'umask 077'
  local key value action="${1:-}"
  for key in GREEN_MIG_RELAY_PASS GREEN_MIG_B_PASS GREEN_MIG_RABBIT_PASS GREEN_MIG_DEBUG GREEN_V11_RELAY_SCRIPT_DIR GREEN_V11_TARGET_SCRIPT_DIR; do
    case $key in
      GREEN_MIG_RELAY_PASS)
        case $action in backup-to-relay|precheck-restore|restore-from-relay) value="${RELAY_PASS:-}";; *) value='';; esac ;;
      GREEN_MIG_B_PASS)
        case $action in stage) value="${TARGET_PASS:-}";; setup-ssh-key-auth) value="${RELAY_PASS:-}";; *) value='';; esac ;;
      GREEN_MIG_RABBIT_PASS) value='' ;;
      GREEN_MIG_DEBUG) value="${DEBUG_LOGS:-false}" ;;
      *) value="${!key:-}" ;;
    esac
    printf 'export %s=%q\n' "$key" "$value"
  done
  printf 'export GREEN_MIG_KNOWN_HOSTS=%q\n' "$dir/known_hosts"
  printf 'export PATH=%q:"$PATH"\n' "$dir"
  printf 'cd -- %q\nexec bash %q' "$dir" "$dir/$program"
  printf ' %q' "$@"
  printf '\n'
}
deploy() {
  local role="$1" host="$2" user="$3" password="$4" base="$5" dir file upload local_stage
  dir="${base%/}/v1.1-$GREEN_V11_SESSION"
  upload="$dir/.upload-$GREEN_V11_SESSION-$$"
  local_stage="$(mktemp -d "$GREEN_MIG_SCRATCH/release.XXXXXXXX")"
  local -a files=(green_migration_tool.sh green_v1_services.sh green_v11_image.py green_v1_target_start.sh exclude.txt items_rootpkg.txt known_hosts)
  [[ -z ${ITEM_FILE:-} ]] || files+=(requested-items.txt)
  [[ -z ${RESTORE_ALLOWLIST:-} ]] || files+=(restore-allowlist.txt)
  [[ -z ${SOURCE_DESIRED_SERVICES_FILE:-} ]] || files+=(requested-desired-services.txt)
  for file in "${files[@]}"; do
    local source_file="$SCRIPT_DIR/$file"
    [[ $file != known_hosts ]] || source_file="$KNOWN_HOSTS_FILE"
    [[ $file != requested-items.txt ]] || source_file="$ITEM_FILE"
    [[ $file != restore-allowlist.txt ]] || source_file="$RESTORE_ALLOWLIST"
    [[ $file != requested-desired-services.txt ]] || source_file="$SOURCE_DESIRED_SERVICES_FILE"
    cp -- "$source_file" "$local_stage/$file"
  done
  (cd -- "$local_stage" && sha256sum -- "${files[@]}" > deploy.sha256)
  # Publish a versioned directory; never overwrite an installed V1 script or an
  # active session's controller. A repeated run verifies exact deployed bytes.
  {
    printf 'set +x\nset -Eeuo pipefail\numask 077\ndir=%q\nupload=%q\n' "$dir" "$upload"
    cat <<'CHECK'
walk=''
IFS=/ read -r -a components <<< "$dir"
for component in "${components[@]}"; do
  [[ -n $component ]] || continue
  walk="$walk/$component"
  [[ ! -L $walk ]] || { echo RELEASE_PARENT_SYMLINK; exit 2; }
done
mkdir -p -- "$dir"
mkdir -- "$upload"
CHECK
  } | remote_script "$host" "$user" "$password"
  local -a sources=()
  for file in "${files[@]}" deploy.sha256; do sources+=("$local_stage/$file"); done
  transport "$password" scp "${ssh_options[@]}" -- "${sources[@]}" "$user@$host:$upload/"
  {
    printf 'set +x\nset -Eeuo pipefail\ndir=%q\nupload=%q\n' "$dir" "$upload"
    printf 'files=('
    printf ' %q' "${files[@]}"
    printf ' )\n'
    cat <<'CHECK'
cd -- "$upload"
sha256sum -c -- deploy.sha256
for file in "${files[@]}"; do
  if [[ -e $dir/$file || -L $dir/$file ]]; then
    [[ -f $dir/$file && ! -L $dir/$file && $(sha256sum "$dir/$file" | cut -d' ' -f1) == $(sha256sum "$file" | cut -d' ' -f1) ]] || { echo RELEASE_DIRECTORY_CONFLICT; exit 2; }
  fi
done
for file in "${files[@]}"; do
  [[ -f $dir/$file ]] || ln -- "$file" "$dir/$file"
done
cd -- "$dir"
[[ $upload == "$dir/".upload-* && ! -L $upload ]] || { echo RELEASE_UPLOAD_BOUNDARY; exit 2; }
rm -r -- "$upload"
CHECK
  } | remote_script "$host" "$user" "$password"
  [[ $local_stage == "${GREEN_MIG_SCRATCH%/}/release."* && ! -L $local_stage ]] || die 'local release staging boundary'
  rm -r -- "$local_stage"
  say "RELEASE_DEPLOYED_ROLE=$role"
}
remote_tool() {
  local host="$1" user="$2" password="$3" base="$4"; shift 4
  bootstrap "${base%/}/v1.1-$GREEN_V11_SESSION" green_migration_tool.sh "$@" | remote_script "$host" "$user" "$password"
}
store_args() { store_options=(--image-store "$IMAGE_STORE" --image-archive-root "$IMAGE_ARCHIVE_ROOT"); }
common_args() {
  tool_options=(--source-root "$SOURCE_ROOT" --exclude-file exclude.txt)
  [[ -z ${ITEM_FILE:-} ]] || tool_options+=(--item-file requested-items.txt)
  [[ -z ${ITEMS:-} ]] || tool_options+=(--items "$ITEMS")
  [[ -z ${MODE:-} ]] || tool_options+=(--mode "$MODE")
  [[ -z ${LIMIT:-} ]] || tool_options+=(-l "$LIMIT")
  local name flag
  for name in RABBIT_USER RABBIT_VHOST RABBIT_TAGS; do
    case $name in RABBIT_USER) flag=--rabbit-user;; RABBIT_VHOST) flag=--rabbit-vhost;; RABBIT_TAGS) flag=--rabbit-tags;; esac
    [[ -z ${!name:-} ]] || tool_options+=("$flag" "${!name}")
  done
}
source_metadata_args() {
  [[ -z ${SOURCE_NODE_ID:-} ]] || tool_options+=(--source-id "$SOURCE_NODE_ID")
  [[ -z ${SOURCE_DESIRED_SERVICES_FILE:-} ]] || tool_options+=(--desired-services requested-desired-services.txt)
  local name flag
  for name in IMAGE_ARCH IMAGE_OS_ID IMAGE_OS_VERSION IMAGE_PRODUCT IMAGE_VERSION; do
    case $name in IMAGE_ARCH) flag=--image-arch;; IMAGE_OS_ID) flag=--image-os-id;; IMAGE_OS_VERSION) flag=--image-os-version;; IMAGE_PRODUCT) flag=--image-product;; IMAGE_VERSION) flag=--image-version;; esac
    [[ -z ${!name:-} ]] || tool_options+=("$flag" "${!name}")
  done
}
restore_args() {
  target_options=(--restore-mode "${RESTORE_MODE:-merge-preserve}" --target-root "${TARGET_ROOT:-/}" --target-mode "${TARGET_MODE:-managed}" --target-service-cycle "${TARGET_SERVICE_CYCLE:-false}" --target-id "$TARGET_NODE_ID" --source-id "$EXPECT_SOURCE_ID" --session-token "$GREEN_V11_SESSION")
  [[ -z ${RESTORE_ALLOWLIST:-} ]] || target_options+=(--allowlist "$GREEN_V11_TARGET_SCRIPT_DIR/restore-allowlist.txt")
  [[ ${IMAGE_DESIRED_SERVICES:-false} != true ]] || target_options+=(--use-image-services)
  local name flag
  for name in IMAGE_ARCH IMAGE_OS_ID IMAGE_OS_VERSION IMAGE_PRODUCT IMAGE_VERSION; do
    case $name in IMAGE_ARCH) flag=--image-arch;; IMAGE_OS_ID) flag=--image-os-id;; IMAGE_OS_VERSION) flag=--image-os-version;; IMAGE_PRODUCT) flag=--image-product;; IMAGE_VERSION) flag=--image-version;; esac
    [[ -z ${!name:-} ]] || target_options+=("$flag" "${!name}")
  done
}
compensate() {
  scratch_init
  [[ -f $GREEN_MIG_SCRATCH/source-cycle-requested ]] || return 0
  if [[ -f $GREEN_MIG_SCRATCH/source-compensation-attempted ]]; then
    [[ ! -f $GREEN_MIG_SCRATCH/source-compensation-completed ]] || return 0
    say "SOURCE_POST_COMPENSATION=PREVIOUSLY_FAILED; RECOVERY_TOKEN=$GREEN_V11_SESSION; no repeated automatic start" >&2
    return 5
  fi
  # Record before transport; a dropped response cannot trigger a restart loop.
  printf '%s\n' "$GREEN_V11_SESSION" > "$GREEN_MIG_SCRATCH/source-compensation-attempted"
  say 'SOURCE_LIFECYCLE=BOUNDED_POST_COMPENSATION'
  if bootstrap "${REMOTE_DIR_SOURCE%/}/v1.1-$GREEN_V11_SESSION" green_v1_services.sh source-resume-if-active "$GREEN_V11_SESSION" | remote_script "$SOURCE_IP" "$SOURCE_USER" "${SOURCE_PASS:-}"; then
    printf '%s\n' "$GREEN_V11_SESSION" > "$GREEN_MIG_SCRATCH/source-compensation-completed"
    say 'SOURCE_POST_COMPENSATION=COMPLETED'
  else
    say "SOURCE_POST_COMPENSATION=BLOCKED; RECOVERY_TOKEN=$GREEN_V11_SESSION; inspect active transfer and saved original state" >&2
    return 5
  fi
}
run() {
  validate; scratch_init; common_args; store_args
  case $ACTION in
    backup-to-relay)
      source_metadata_args
      deploy relay "$RELAY_IP" "$RELAY_USER" "${RELAY_PASS:-}" "$REMOTE_DIR_RELAY"
      deploy source "$SOURCE_IP" "$SOURCE_USER" "${SOURCE_PASS:-}" "$REMOTE_DIR_SOURCE"
      [[ ${SOURCE_SERVICE_CYCLE:-false} != true ]] || printf '%s\n' "$GREEN_V11_SESSION" > "$GREEN_MIG_SCRATCH/source-cycle-requested"
      remote_tool "$SOURCE_IP" "$SOURCE_USER" "${SOURCE_PASS:-}" "$REMOTE_DIR_SOURCE" "$ACTION" "${tool_options[@]}" "${store_options[@]}" --relay-ip "$RELAY_IP" --relay-user "$RELAY_USER" --relay-root "$RELAY_ROOT" --backup-name "$BACKUP_NAME" --source-service-cycle "${SOURCE_SERVICE_CYCLE:-false}" --session-token "$GREEN_V11_SESSION"
      ;;
    precheck-restore|restore-from-relay)
      deploy relay "$RELAY_IP" "$RELAY_USER" "${RELAY_PASS:-}" "$REMOTE_DIR_RELAY"
      deploy target "$TARGET_IP" "$TARGET_USER" "${TARGET_PASS:-}" "$REMOTE_DIR_TARGET"
      restore_args
      remote_tool "$TARGET_IP" "$TARGET_USER" "${TARGET_PASS:-}" "$REMOTE_DIR_TARGET" "$ACTION" "${store_options[@]}" "${target_options[@]}" --relay-ip "$RELAY_IP" --relay-user "$RELAY_USER" --relay-root "$RELAY_ROOT" --backup-id "$BACKUP_ID" --exclude-file exclude.txt
      ;;
    list-relay-backups|list-archived-images)
      # The tool already lists native and archived images through this relay.
      # Do not invoke a relay-local tool with empty second-hop SSH parameters.
      GREEN_MIG_RELAY_PASS="${RELAY_PASS:-}" GREEN_MIG_KNOWN_HOSTS="$KNOWN_HOSTS_FILE" bash "$SCRIPT_DIR/green_migration_tool.sh" "$ACTION" --relay-ip "$RELAY_IP" --relay-user "$RELAY_USER" --relay-root "$RELAY_ROOT" --exclude-file "$SCRIPT_DIR/exclude.txt" "${store_options[@]}"
      ;;
    setup-ssh-key-auth)
      GREEN_MIG_B_PASS="${TARGET_PASS:-}" GREEN_MIG_KNOWN_HOSTS="$KNOWN_HOSTS_FILE" bash "$SCRIPT_DIR/green_migration_tool.sh" "$ACTION" -b "$TARGET_IP" -u "$TARGET_USER" --exclude-file "$SCRIPT_DIR/exclude.txt"
      if [[ $TRANSFER_MODE == relay ]]; then
        GREEN_MIG_B_PASS="${RELAY_PASS:-}" GREEN_MIG_KNOWN_HOSTS="$KNOWN_HOSTS_FILE" bash "$SCRIPT_DIR/green_migration_tool.sh" "$ACTION" -b "$RELAY_IP" -u "$RELAY_USER" --exclude-file "$SCRIPT_DIR/exclude.txt"
        deploy target "$TARGET_IP" "$TARGET_USER" "${TARGET_PASS:-}" "$REMOTE_DIR_TARGET"
        remote_tool "$TARGET_IP" "$TARGET_USER" "${TARGET_PASS:-}" "$REMOTE_DIR_TARGET" "$ACTION" -b "$RELAY_IP" -u "$RELAY_USER" --exclude-file exclude.txt
      fi
      ;;
    stage)
      source_metadata_args
      deploy target "$TARGET_IP" "$TARGET_USER" "${TARGET_PASS:-}" "$REMOTE_DIR_TARGET"
      deploy source "$SOURCE_IP" "$SOURCE_USER" "${SOURCE_PASS:-}" "$REMOTE_DIR_SOURCE"
      remote_tool "$SOURCE_IP" "$SOURCE_USER" "${SOURCE_PASS:-}" "$REMOTE_DIR_SOURCE" "$ACTION" "${tool_options[@]}" -b "$TARGET_IP" -u "$TARGET_USER"
      ;;
    apply)
      deploy target "$TARGET_IP" "$TARGET_USER" "${TARGET_PASS:-}" "$REMOTE_DIR_TARGET"
      restore_args
      GREEN_MIG_B_PASS="${TARGET_PASS:-}" GREEN_MIG_KNOWN_HOSTS="$KNOWN_HOSTS_FILE" bash "$SCRIPT_DIR/green_migration_tool.sh" "$ACTION" "${tool_options[@]}" "${target_options[@]}" --stage-id "${TARGET_STAGE_ID:-$STAGE_ID}" -b "$TARGET_IP" -u "$TARGET_USER"
      ;;
    precheck)
      GREEN_MIG_B_PASS="${TARGET_PASS:-}" GREEN_MIG_KNOWN_HOSTS="$KNOWN_HOSTS_FILE" bash "$SCRIPT_DIR/green_migration_tool.sh" "$ACTION" "${tool_options[@]}" -b "$TARGET_IP" -u "$TARGET_USER"
      ;;
  esac
}
on_exit() {
  local result=$?
  trap - EXIT INT TERM
  if [[ ${GREEN_V11_COMMAND:-} == run && -n ${GREEN_MIG_SCRATCH:-} ]]; then
    if ! compensate; then [[ $result != 0 ]] || result=5; fi
  fi
  exit "$result"
}
main() {
  GREEN_V11_COMMAND="${1:-help}"
  if [[ $GREEN_V11_COMMAND == help || $GREEN_V11_COMMAND == --help ]]; then say 'Usage: bash green_v11_jenkins.sh validate|run|compensate (Job parameters in environment)'; return; fi
  defaults
  case $GREEN_V11_COMMAND in
    validate) validate ;;
    run) trap on_exit EXIT; trap 'exit 130' INT; trap 'exit 143' TERM; run ;;
    compensate) compensate ;;
    *) die 'invalid orchestration command' ;;
  esac
}
if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
