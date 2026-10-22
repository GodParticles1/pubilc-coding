#!/usr/bin/env bash
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# V1.1 adds explicit safety contracts while retaining the existing transport flags.
RESTORE_MODE="merge-preserve"
TARGET_ROOT="/"
TARGET_MODE="managed"
TARGET_ID=""
IMAGE_SOURCE_ID="${GREEN_V11_SOURCE_ID:-}"
IMAGE_ARCH="${GREEN_V11_IMAGE_ARCH:-}"
IMAGE_OS_ID="${GREEN_V11_OS_ID:-}"
IMAGE_OS_VERSION="${GREEN_V11_OS_VERSION:-}"
IMAGE_PRODUCT="${GREEN_V11_PRODUCT:-UNVERIFIED}"
IMAGE_VERSION="${GREEN_V11_VERSION:-UNVERIFIED}"
IMAGE_DIR=""
IMAGE_ARCHIVE=""
IMAGE_STORE="none"
IMAGE_ARCHIVE_ROOT=""
SOURCE_SERVICE_CYCLE="false"
TARGET_SERVICE_CYCLE="false"
SESSION_TOKEN=""
ALLOWLIST=""
RESTORE_STATE="${GREEN_V11_RESTORE_STATE:-/var/lib/green-v11}"
STAGE_ID=""
DESIRED_SERVICES=""
USE_IMAGE_SERVICES=false
V11_SCRATCH=""
V11_FROZEN=false
V11_LEASE=false
V11_ABORTED=false
V11_BACKUP_DIR=""
V11_BACKUP_OK=false
V11_COPY_LOCK=""
SELF_NAME="green_migration_tool.sh"
EXCLUDE_FILE_DEFAULT="$SCRIPT_DIR/exclude.txt"
TIMESTAMP="$(date +'%Y%m%d_%H%M%S')"
# No on-disk migration log in the tool/script directory; Jenkins console owns logs.
# Set GREEN_MIG_DEBUG=true only for detailed console progress (never bash -x).
GREEN_MIG_DEBUG="${GREEN_MIG_DEBUG:-false}"
LOG_FILE=/dev/null

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
RABBIT_PASS=""
RABBIT_VHOST="/"
RABBIT_TAGS="administrator"

SSH_KEY_FILE="${HOME}/.ssh/id_rsa"

# Jenkins / CI 场景可通过环境变量注入密码，避免把密码硬编码在命令行
GREEN_MIG_B_PASS="${GREEN_MIG_B_PASS:-}"
GREEN_MIG_RELAY_PASS="${GREEN_MIG_RELAY_PASS:-}"
GREEN_MIG_SOURCE_PASS="${GREEN_MIG_SOURCE_PASS:-}"
GREEN_MIG_RABBIT_PASS="${GREEN_MIG_RABBIT_PASS:-}"

DEFAULT_ITEMS=(/opt /etc /var /bin /sbin /lib /lib64 /usr)

INCREMENTAL_EXTRA_DIRS=()
OPT_INCREMENTAL_WHITELIST=()
ETC_INCREMENTAL_WHITELIST=()
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
log_info(){ printf '[%s] [INFO] %s\n' "$(ts)" "$*"; }
log_warn(){ printf '[%s] [WARN] %s\n' "$(ts)" "$*"; }
log_error(){ printf '[%s] [ERROR] %s\n' "$(ts)" "$*" >&2; }
log_debug(){ case "$GREEN_MIG_DEBUG" in true|TRUE|1) printf '[%s] [DEBUG] %s\n' "$(ts)" "$*" ;; esac; }

ssh_opts=(-o StrictHostKeyChecking=yes -o "UserKnownHostsFile=${GREEN_MIG_KNOWN_HOSTS:-$HOME/.ssh/known_hosts}" -o LogLevel=ERROR -o ConnectTimeout=10 -o ServerAliveInterval=15 -o ServerAliveCountMax=3)

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

V1.1 安全入口：restore-image | plan-image | recover-image | list-archived-images
恢复必选：--restore-mode merge-preserve|replace-managed --target-root ROOT
          --target-mode managed|offline --allowlist FILE --session-token TOKEN
源自动生命周期：--source-service-cycle true（仅 backup-to-relay、SOURCE_ROOT=/）
目标自动生命周期：--target-service-cycle true（仅经审核的窄管理树）
完整根镜像必须离线挂载；身份、版本、target profile 与完整参数见同目录 README.md。
recover-image 使用原始 --image-dir 路径与精确 token/目标契约读取已保留日志，不重新展开归档。
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

      --restore-mode) RESTORE_MODE="$2"; shift 2 ;;
      --target-root) TARGET_ROOT="$2"; shift 2 ;;
      --target-mode) TARGET_MODE="$2"; shift 2 ;;
      --target-id) TARGET_ID="$2"; shift 2 ;;
      --source-id) IMAGE_SOURCE_ID="$2"; shift 2 ;;
      --image-arch) IMAGE_ARCH="$2"; shift 2 ;;
      --image-os-id) IMAGE_OS_ID="$2"; shift 2 ;;
      --image-os-version) IMAGE_OS_VERSION="$2"; shift 2 ;;
      --image-product) IMAGE_PRODUCT="$2"; shift 2 ;;
      --image-version) IMAGE_VERSION="$2"; shift 2 ;;
      --image-dir) IMAGE_DIR="$2"; shift 2 ;;
      --image-archive) IMAGE_ARCHIVE="$2"; shift 2 ;;
      --image-store) IMAGE_STORE="$2"; shift 2 ;;
      --image-archive-root) IMAGE_ARCHIVE_ROOT="$2"; shift 2 ;;
      --source-service-cycle) SOURCE_SERVICE_CYCLE="$2"; shift 2 ;;
      --target-service-cycle) TARGET_SERVICE_CYCLE="$2"; shift 2 ;;
      --session-token) SESSION_TOKEN="$2"; shift 2 ;;
      --allowlist) ALLOWLIST="$2"; shift 2 ;;
      --state-dir) RESTORE_STATE="$2"; shift 2 ;;
      --stage-id) STAGE_ID="$2"; shift 2 ;;
      --desired-services) DESIRED_SERVICES="$2"; shift 2 ;;
      --use-image-services) USE_IMAGE_SERVICES=true; shift ;;

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



ssh_cmd(){
  local pass="$1"; shift
  if [[ -n "$pass" ]]; then
    have_sshpass || { log_error 'password authentication requires sshpass; use SSH keys otherwise'; return 2; }
    SSHPASS="$pass" "$SSHPASS_BIN" -e ssh "${ssh_opts[@]}" "$@"
  else
    ssh -o BatchMode=yes "${ssh_opts[@]}" "$@"
  fi
}

scp_cmd(){
  local pass="$1"; shift
  if [[ -n "$pass" ]]; then
    have_sshpass || { log_error 'password authentication requires sshpass'; return 2; }
    SSHPASS="$pass" "$SSHPASS_BIN" -e scp "${ssh_opts[@]}" "$@"
  else
    scp -o BatchMode=yes "${ssh_opts[@]}" "$@"
  fi
}

rsync_ssh_cmd(){
  local pass="$1"
  if have_sshpass && [[ -n "$pass" ]]; then printf '%q -e ' "$SSHPASS_BIN"; fi
  printf 'ssh '; [[ -n "$pass" ]] || printf '%s ' '-o BatchMode=yes'; printf '%q ' "${ssh_opts[@]}"
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
  case "$GREEN_MIG_DEBUG" in true|TRUE|1) ;; *) return 0 ;; esac
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
    v11_path "$p"
    [[ "$p" != / ]] || v11_fail 'cannot migrate the whole root as one item'
    case "$p" in /*) ;; *) log_error "迁移项必须是绝对路径：$p"; exit 1 ;; esac
    for b in "${BLOCKED_ITEMS[@]}"; do
      if [[ "$p" == "$b" || "$p" == "$b/"* ]]; then
        log_error "迁移项命中系统关键路径，禁止迁移：$p"
        exit 1
      fi
    done
    if source_path_exists "$p"; then
      echo "$p" >> "$checked"
    elif [[ -n "$ITEM_FILE" || -n "$ITEMS_CSV" ]]; then
      v11_fail 'explicit migration item does not exist'
    fi
  done < "$outfile"
  mv "$checked" "$outfile"
  [[ -s "$outfile" ]] || v11_fail 'no existing migration items'
  [[ $(sort -u "$outfile" | wc -l) == $(wc -l < "$outfile") ]] || v11_fail 'duplicate migration items'
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





















ensure_local_ssh_key(){
  local key_dir pub_file
  key_dir="$(dirname "$SSH_KEY_FILE")"; pub_file="${SSH_KEY_FILE}.pub"
  if [[ -f "$SSH_KEY_FILE" && -f "$pub_file" ]]; then return 0; fi
  [[ ! -e "$SSH_KEY_FILE" && ! -e "$pub_file" ]] || { log_error 'incomplete existing SSH key pair; refusing overwrite'; return 2; }
  need_cmd ssh-keygen
  mkdir -p -- "$key_dir"; chmod 700 "$key_dir"
  ssh-keygen -t ed25519 -N '' -f "$SSH_KEY_FILE" >/dev/null
}

setup_remote_ssh_key_auth(){
  local remote_user="$1" remote_host="$2" remote_pass="$3"
  ensure_local_ssh_key; need_cmd ssh-copy-id
  if [[ -n "$remote_pass" ]]; then
    have_sshpass || { log_error 'sshpass required for password bootstrap'; return 2; }
    SSHPASS="$remote_pass" "$SSHPASS_BIN" -e ssh-copy-id -i "${SSH_KEY_FILE}.pub" "${ssh_opts[@]}" "$remote_user@$remote_host"
  else
    ssh-copy-id -i "${SSH_KEY_FILE}.pub" "${ssh_opts[@]}" "$remote_user@$remote_host"
  fi
  ssh -i "$SSH_KEY_FILE" -o IdentitiesOnly=yes -o BatchMode=yes "${ssh_opts[@]}" "$remote_user@$remote_host" true
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






precheck_action(){
  pre_basic_direct_checks
  local items_file="$V11_SCRATCH/precheck-items.txt"
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

apply_action(){ v11_apply_direct; }

source_image_needs_freeze(){ [[ "$SOURCE_ROOT" == "/" ]]; }

# Normalize process status from exactly recognized Monit/Gonit summary layouts:
#   Monit 5.x: Service Name | Status | Type (box-drawn or ASCII)
#   Gonit:     Service Name | Cluster | Local | Type | Hosts | Uptime
# Legacy plain (Service Name Status Type) is also covered. Do not assume
# the last column is Type: in Gonit it is Uptime. Fail closed on unknown status.
# Output: service<TAB>local_status. System rows are never returned.


source_cold_state_check(){
  source_image_needs_freeze || return 0
  bash "$SCRIPT_DIR/green_v1_services.sh" cold-check
}

start_source_cold_watch(){
  source_image_needs_freeze || return 0
  source_cold_state_check || return $?
  GREEN_MIG_COLD_WATCH_DIR="$(mktemp -d /tmp/green-mig-cold.XXXXXXXX)"
  # Periodic check is supplemental; it cannot replace DB-consistent shutdown.
  (
    while sleep "${GREEN_MIG_GUARD_INTERVAL:-10}"; do
      if ! source_cold_state_check; then
        printf 'SOURCE_RESTARTED_DURING_BACKUP\n' > "$GREEN_MIG_COLD_WATCH_DIR/unsafe"
        break
      fi
    done
  ) &
  GREEN_MIG_COLD_WATCH_PID=$!
  log_info 'SOURCE_IMAGE_FREEZE_CHECK=PASS; monitoring during backup'
}

assert_source_cold_watch(){
  source_image_needs_freeze || return 0
  if [[ -e "$GREEN_MIG_COLD_WATCH_DIR/unsafe" ]]; then
    log_error 'SOURCE_IMAGE_INVALID: application restarted during transfer; do not use this partial backup'
    return 83
  fi
  source_cold_state_check || { log_error 'SOURCE_IMAGE_INVALID: writer appeared during transfer'; return 83; }
  return 0
}

stop_source_cold_watch(){
  if [[ -n "${GREEN_MIG_COLD_WATCH_PID:-}" ]]; then
    kill "$GREEN_MIG_COLD_WATCH_PID" 2>/dev/null || true
    wait "$GREEN_MIG_COLD_WATCH_PID" 2>/dev/null || true
    GREEN_MIG_COLD_WATCH_PID=""
  fi
  if [[ -n "${GREEN_MIG_COLD_WATCH_DIR:-}" && -d "$GREEN_MIG_COLD_WATCH_DIR" ]]; then
    rm -rf -- "$GREEN_MIG_COLD_WATCH_DIR"
    GREEN_MIG_COLD_WATCH_DIR=""
  fi
}

backup_to_relay_rsync(){
  local relay_dir="$1" items_file="$2"
  local progress_opt p rc=0
  local -a backup_items=() source_args=() rsync_opts=()
  progress_opt="$(detect_rsync_progress_opt)"
  rsync_opts=(-aH --relative --numeric-ids --links --partial --human-readable --exclude-from="$EXCLUDE_FILE" --log-file="$LOG_FILE")
  [[ -n "$progress_opt" ]] && rsync_opts+=("$progress_opt")
  [[ -n "$LIMIT" ]] && rsync_opts+=(--bwlimit="$LIMIT")
  mapfile -t backup_items < "$items_file"
  for p in "${backup_items[@]}"; do
    [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] || continue
    # The /./ cut point keeps /opt/... rooted in the image, so root-anchored
    # exclusions work. A single -H transfer retains links across item roots.
    source_args+=("${SOURCE_ROOT%/}/.${p}")
  done
  ((${#source_args[@]})) || { log_error 'all rsync source items vanished'; return 24; }
  relay_exec "umask 077; mkdir -p '$relay_dir/files'" >/dev/null || return $?
  "$RSYNC_BIN" "${rsync_opts[@]}" --rsync-path="$(relay_rsync_path)" -e "$(rsync_ssh_cmd "$RELAY_PASS")" "${source_args[@]}" "$RELAY_USER@$RELAY_IP:$relay_dir/files/" || rc=$?
  if (( rc != 0 )); then
    if (( rc == 24 )); then
      log_error "RSYNC_CODE_24: source files vanished while copying; partial backup stays .inprogress"
      log_error "Check whether applications wrote/deleted files during transfer; never accept this image as complete"
    fi
    return "$rc"
  fi
  assert_source_cold_watch || return $?
  backup_extra_incremental_rsync "$relay_dir"
}

backup_to_relay_tar(){
  local relay_dir="$1" items_file="$2"
  local tar_mode listfile p effective_excludes="$V11_SCRATCH/tar-excludes.txt"
  local -a backup_items=()
  tar_mode="$(detect_source_tar_exclude_support)"
  [[ "$tar_mode" == "none" ]] && { log_error "源端 tar 不支持排除清单"; return 2; }
  # Tar receives ./opt/... members. Preserve root-anchored exclude semantics.
  awk 'NF && $0 !~ /^[[:space:]]*#/ {if (substr($0,1,1)=="/") print "." $0; else print}' "$EXCLUDE_FILE" > "$effective_excludes" || return $?
  relay_exec "umask 077; mkdir -p '$relay_dir/files'" >/dev/null || return $?
  listfile="$V11_SCRATCH/tar-items.txt"; : > "$listfile"
  mapfile -t backup_items < "$items_file"
  for p in "${backup_items[@]}"; do
    [[ -e "${SOURCE_ROOT%/}${p}" || -L "${SOURCE_ROOT%/}${p}" ]] && echo ".${p}" >> "$listfile"
  done
  if [[ "$tar_mode" == "exclude" ]]; then
    (cd "$SOURCE_ROOT" && tar cpf - --exclude-from="$effective_excludes" -T "$listfile") | relay_exec "tar xpf - -C '$relay_dir/files'" || return $?
  else
    (cd "$SOURCE_ROOT" && tar cpf - -X "$effective_excludes" -T "$listfile") | relay_exec "tar xpf - -C '$relay_dir/files'" || return $?
  fi
  backup_extra_incremental_tar "$relay_dir"
}

backup_to_relay_action(){
  pre_basic_relay_checks
  [[ -n "$BACKUP_NAME" ]] || { log_error "backup-to-relay requires --backup-name"; exit 1; }
  local items_file="$V11_SCRATCH/items.txt"
  local manifest="$V11_SCRATCH/manifest.txt"
  local owner_manifest="$V11_SCRATCH/owners.tsv"
  local relay_dir selected_mode backup_id incomplete
  resolve_items_to_file "$items_file"
  # Fast, fail-closed cold image check BEFORE any multi-GB source transfer.
  start_source_cold_watch || return $?
  selected_mode="$(choose_relay_mode)"

  # Never silently reuse a potentially dirty image created when services ran.
  # Keep the previous .inprogress tree for manual review; create a new timestamp.
  if [[ "$selected_mode" == "rsync" ]]; then
    incomplete="$(find_relay_resumable_dir)"
    if [[ -n "$incomplete" ]]; then
      log_warn "OLD_INCOMPLETE_PRESERVED_NOT_REUSED=$incomplete"
    fi
  fi
  relay_dir="$(relay_backup_dir)"
  backup_id="${BACKUP_NAME}/${TIMESTAMP}"
  v11_reserve_remote "$relay_dir" || return 84

  if [[ "$selected_mode" == "rsync" ]]; then
    backup_to_relay_rsync "$relay_dir" "$items_file" || return $?
  else
    backup_to_relay_tar "$relay_dir" "$items_file" || return $?
  fi
  assert_source_cold_watch || return $?
  write_manifest_local "$manifest" "$selected_mode" "$items_file"
  generate_owner_manifest_local "$owner_manifest" "$items_file"
  assert_source_cold_watch || return $?
  scp_cmd "$RELAY_PASS" "$manifest" "$RELAY_USER@$RELAY_IP:$relay_dir/manifest.txt" >/dev/null 2>&1
  scp_cmd "$RELAY_PASS" "$owner_manifest" "$RELAY_USER@$RELAY_IP:$relay_dir/owners.tsv" >/dev/null 2>&1
  assert_source_cold_watch || return $?
  v11_seal_remote "$relay_dir"
  assert_source_cold_watch || { relay_exec "touch '$relay_dir/.inprogress'"; return 83; }
  v11_archive_remote "$relay_dir" "$backup_id"
  assert_source_cold_watch || { relay_exec "touch '$relay_dir/.inprogress'"; return 83; }
  stop_source_cold_watch
  log_info "BACKUP_OK=$backup_id"
  # Preserve the legacy Chinese success marker used by Jenkins backup-id parsing.
  log_info "backup-to-relay 完成。备份 ID：$backup_id"
}

list_relay_backups_action(){
  pre_basic_relay_checks
  relay_exec "find '$RELAY_ROOT' -mindepth 2 -maxdepth 2 -type d 2>/dev/null | sed 's#^$RELAY_ROOT/##' | sort"
}

precheck_restore_action(){ v11_restore_relay plan; }

restore_from_relay_action(){ v11_restore_relay apply; }

v11_fail(){ log_error "BLOCKED: $*"; exit 2; }
v11_path(){
  [[ "$1" =~ ^/[A-Za-z0-9_./-]*$ && "$1/" != *'/../'* && "$1/" != *'/./'* && "$1" != *'//'* ]] || v11_fail 'unsafe absolute path';
}
v11_quote(){ printf '%q ' "$@"; }
v11_python(){ "${GREEN_MIG_PYTHON:-python3}" "$SCRIPT_DIR/green_v11_image.py" "$@"; }
v11_relay_image(){ relay_exec "$(v11_quote "${GREEN_V11_RELAY_PYTHON:-python3}" "$V11_RELAY_SCRIPTS/green_v11_image.py" "$@")"; }
v11_local_id(){
  [[ -s /etc/machine-id ]] || v11_fail 'machine-id unavailable; supply source identity for a directory fixture';
  tr -d '\r\n' < /etc/machine-id
}
v11_os_value(){ awk -F= -v key="$1" '$1==key {gsub(/"/, "", $2); print $2}' "$SOURCE_ROOT/etc/os-release" 2>/dev/null || true; }
v11_validate(){
  local p value
  for p in "$REMOTE_STAGE_ROOT" "$RELAY_ROOT" "$SOURCE_ROOT" "$TARGET_ROOT" "$RESTORE_STATE"; do v11_path "$p"; done
  [[ "$RELAY_ROOT" != / && "$REMOTE_STAGE_ROOT" != / && "$RESTORE_STATE" != / ]] || v11_fail 'storage root cannot be /'
  [[ "$SOURCE_SERVICE_CYCLE" == true || "$SOURCE_SERVICE_CYCLE" == false ]] || v11_fail 'invalid source lifecycle option'
  [[ "$TARGET_SERVICE_CYCLE" == true || "$TARGET_SERVICE_CYCLE" == false ]] || v11_fail 'invalid target lifecycle option'
  [[ "$SOURCE_SERVICE_CYCLE" != true || ( "$ACTION" == backup-to-relay && "$SOURCE_ROOT" == / ) ]] || v11_fail 'source lifecycle is backup-to-relay root-image only'
  [[ "$RESTORE_MODE" == merge-preserve || "$RESTORE_MODE" == replace-managed ]] || v11_fail 'invalid restore mode'
  [[ "$MODE" == auto || "$MODE" == rsync || "$MODE" == tar-stream ]] || v11_fail 'invalid transfer mode'
  [[ "$TARGET_MODE" != fixture ]] || v11_fail 'fixture is test-only and unavailable from the runtime Shell entry'
  [[ "$TARGET_MODE" == managed || "$TARGET_MODE" == offline ]] || v11_fail 'invalid target mode'
  [[ "$IMAGE_STORE" == none || "$IMAGE_STORE" == local || "$IMAGE_STORE" == webdav ]] || v11_fail 'invalid IMAGE_STORE'
  if [[ -z "$IMAGE_ARCHIVE_ROOT" ]]; then
    if [[ "$IMAGE_STORE" == webdav ]]; then IMAGE_ARCHIVE_ROOT=/opt/data/webdav/Base/auguImages
    else IMAGE_ARCHIVE_ROOT=/opt/data/green-migration-images; fi
  fi
  v11_path "$IMAGE_ARCHIVE_ROOT"
  [[ "$IMAGE_ARCHIVE_ROOT" != / ]] || v11_fail 'archive root cannot be /'
  for value in "$B_USER" "$RELAY_USER"; do [[ -z "$value" || "$value" =~ ^[A-Za-z_][A-Za-z0-9_.-]*$ ]] || v11_fail 'invalid SSH user'; done
  for value in "$B_IP" "$RELAY_IP"; do [[ -z "$value" || "$value" =~ ^[A-Za-z0-9_.:-]+$ ]] || v11_fail 'invalid SSH host'; done
  [[ -z "$LIMIT" || "$LIMIT" =~ ^[0-9]+[kKmMgG]?$ ]] || v11_fail 'invalid bandwidth limit'
  [[ -z "$BACKUP_NAME" || "$BACKUP_NAME" =~ ^[A-Za-z0-9_-]{1,80}$ ]] || v11_fail 'unsafe backup name'
  [[ -z "$BACKUP_ID" || "$BACKUP_ID" =~ ^[A-Za-z0-9_-]+/[0-9]{8}_[0-9]{6}$ ]] || v11_fail 'unsafe backup ID'
  if [[ -z "$SESSION_TOKEN" ]]; then SESSION_TOKEN="local-${TIMESTAMP}-$$"; fi
  [[ "$SESSION_TOKEN" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$ ]] || v11_fail 'invalid session token'
  V11_RELAY_SCRIPTS="${GREEN_V11_RELAY_SCRIPT_DIR:-$SCRIPT_DIR}"
  v11_path "$V11_RELAY_SCRIPTS"
  for value in "$IMAGE_SOURCE_ID" "$TARGET_ID" "$IMAGE_ARCH" "$IMAGE_OS_ID" "$IMAGE_OS_VERSION" "$IMAGE_PRODUCT" "$IMAGE_VERSION"; do
    [[ -z "$value" || "$value" =~ ^[A-Za-z0-9_.:+-]{1,128}$ ]] || v11_fail 'unsafe identity/version value'
  done
}
v11_archive_store_check(){
  # Preserve the previously supported pre-mounted WebDAV storage contract.
  local mode="$IMAGE_STORE" root="$IMAGE_ARCHIVE_ROOT"
  [[ "$mode" != none ]] || return 0
  if [[ "$mode" == webdav ]]; then
    [[ "$root" == /opt/data/webdav/Base/* ]] || v11_fail 'WebDAV store must be under the approved mount'
    relay_exec "mountpoint -q /opt/data/webdav/Base && test -d '$root'" || v11_fail 'WebDAV is not mounted'
  else
    [[ "$root" != /opt/data/webdav/* ]] || v11_fail 'local store must not use WebDAV path'
    relay_exec "umask 077; mkdir -p '$root' && test \"\$(findmnt -T '$root' -n -o FSTYPE)\" != fuse.rclone" || v11_fail 'local archive requires native storage'
  fi
}
v11_backup_preflight(){
  local actual relay_id actual_arch actual_os actual_version
  [[ -n "$IMAGE_SOURCE_ID" ]] || IMAGE_SOURCE_ID="$(v11_local_id)"
  if [[ "$SOURCE_ROOT" == / ]]; then
    actual="$(v11_local_id)"
    [[ "$actual" == "$IMAGE_SOURCE_ID" ]] || v11_fail 'source identity differs from this host'
    actual_arch="$(uname -m)"; actual_os="$(v11_os_value ID)"; actual_version="$(v11_os_value VERSION_ID)"
    [[ -n "$actual_arch" && -n "$actual_os" && -n "$actual_version" ]] || v11_fail 'source architecture/OS facts unavailable'
    [[ -z "$IMAGE_ARCH" || "$IMAGE_ARCH" == "$actual_arch" ]] || v11_fail 'source architecture expectation mismatch'
    [[ -z "$IMAGE_OS_ID" || "$IMAGE_OS_ID" == "$actual_os" ]] || v11_fail 'source OS expectation mismatch'
    [[ -z "$IMAGE_OS_VERSION" || "$IMAGE_OS_VERSION" == "$actual_version" ]] || v11_fail 'source OS version expectation mismatch'
    IMAGE_ARCH="$actual_arch"; IMAGE_OS_ID="$actual_os"; IMAGE_OS_VERSION="$actual_version"
    relay_id="$(relay_exec 'cat /etc/machine-id')" || v11_fail 'unable to verify relay identity'
    [[ -n "$relay_id" && "$relay_id" != "$actual" ]] || v11_fail 'source and relay are the same machine'
  fi
  [[ -n "$IMAGE_ARCH" ]] || IMAGE_ARCH="$(uname -m)"
  if [[ -z "$IMAGE_OS_ID" ]]; then IMAGE_OS_ID="$(v11_os_value ID)"; fi
  if [[ -z "$IMAGE_OS_VERSION" ]]; then IMAGE_OS_VERSION="$(v11_os_value VERSION_ID)"; fi
  IMAGE_OS_ID="${IMAGE_OS_ID:-UNVERIFIED}"; IMAGE_OS_VERSION="${IMAGE_OS_VERSION:-UNVERIFIED}"
  relay_exec "$(v11_quote "${GREEN_V11_RELAY_PYTHON:-python3}" --version) >/dev/null && test -f '$V11_RELAY_SCRIPTS/green_v11_image.py'" || v11_fail 'relay Python3/image helper unavailable'
  local fs_probe
  fs_probe="probe=$(printf '%q' "$RELAY_ROOT"); while [ ! -e \"\$probe\" ]; do probe=\$(dirname \"\$probe\"); done; fs=\$(findmnt -T \"\$probe\" -n -o FSTYPE); [ -n \"\$fs\" ] && [ \"\$fs\" != fuse.rclone ]"
  relay_exec "$fs_probe" || v11_fail 'relay working image must use verified native storage, not mounted WebDAV'
  v11_archive_store_check
  local selected
  selected="$(choose_relay_mode)"
  if [[ "$selected" == rsync ]]; then
    have_rsync && relay_has_cmd rsync || v11_fail 'requested rsync unavailable on source/relay'
  else
    relay_has_cmd tar || v11_fail 'relay tar unavailable'
    [[ "$(detect_source_tar_exclude_support)" != none ]] || v11_fail 'source tar lacks exclusions'
  fi
}
v11_reserve_remote(){
  local image="$1"
  # mkdir is the collision gate. No check-then-overwrite, no reuse of partial images.
  local code='import pathlib,sys,os; os.umask(0o077); p=pathlib.Path(sys.argv[1]); any(a.is_symlink() for a in (p,*p.parents)) and sys.exit("symlink storage parent"); p.parent.mkdir(parents=True,exist_ok=True); os.mkdir(p); (p/"files").mkdir(); (p/".inprogress").write_text(sys.argv[2],encoding="utf-8")'
  relay_exec "$(v11_quote "${GREEN_V11_RELAY_PYTHON:-python3}" -c "$code" "$image" "$SESSION_TOKEN")" || return $?
  V11_BACKUP_DIR="$image"
}
v11_seal_remote(){
  local image="$1"
  scp_cmd "$RELAY_PASS" "$EXCLUDE_FILE" "$RELAY_USER@$RELAY_IP:$image/exclude.txt"
  local -a desired=()
  if [[ -n "$DESIRED_SERVICES" ]]; then
    [[ -f "$DESIRED_SERVICES" && ! -L "$DESIRED_SERVICES" ]] || v11_fail 'missing desired service profile'
    local profile_dir profile
    profile_dir="$(relay_exec "umask 077; mktemp -d '$V11_RELAY_SCRIPTS/.desired-$SESSION_TOKEN.XXXXXXXX'")" || return $?
    [[ "$profile_dir" == "$V11_RELAY_SCRIPTS/.desired-$SESSION_TOKEN."* ]] || v11_fail 'invalid desired profile scratch directory'
    v11_path "$profile_dir"
    profile="$profile_dir/services.txt"
    scp_cmd "$RELAY_PASS" "$DESIRED_SERVICES" "$RELAY_USER@$RELAY_IP:$profile"
    desired=(--desired-services "$profile")
  fi
  v11_relay_image seal "$image" --source-id "$IMAGE_SOURCE_ID" --arch "$IMAGE_ARCH" --os-id "$IMAGE_OS_ID" --os-version "$IMAGE_OS_VERSION" --product "$IMAGE_PRODUCT" --version "$IMAGE_VERSION" --exclude-file "$image/exclude.txt" "${desired[@]}"
  v11_relay_image verify "$image"
  if [[ -n "$DESIRED_SERVICES" ]]; then relay_exec "rm -f '$profile' && rmdir '$profile_dir'"; fi
}
v11_archive_remote(){
  local image="$1" id="$2"
  [[ "$IMAGE_STORE" != none ]] || { relay_exec "sync"; return 0; }
  local name="${id%%/*}" stamp="${id##*/}" archive="$IMAGE_ARCHIVE_ROOT/${id%%/*}_${id##*/}.tar"
  v11_relay_image archive "$image" "$archive" --prefix "$name/$stamp"
  # archive performs verified publication and readback. Any failure propagates.
  relay_exec "sync"
}
v11_finish(){
  local rc=$? recovery=0
  trap - EXIT INT TERM HUP
  set +e
  stop_source_cold_watch
  if [[ "$V11_ABORTED" == true ]]; then
    log_error "RECOVERY_REQUIRED=$SESSION_TOKEN; abort retains transfer lease and original service state"
    (( rc != 0 )) || rc=130
  else
    if [[ "$V11_LEASE" == true ]]; then
      bash "$SCRIPT_DIR/green_v1_services.sh" source-transfer-end "$SESSION_TOKEN" "$$" || recovery=1
    fi
    if [[ "$V11_FROZEN" == true && "$recovery" == 0 ]]; then
      bash "$SCRIPT_DIR/green_v1_services.sh" source-resume-if-active "$SESSION_TOKEN" || recovery=1
      if (( recovery == 0 )); then log_info 'SOURCE_RECOVERED'; fi
    fi
    if (( recovery != 0 )); then
      log_error "SOURCE_RECOVERY_REQUIRED=$SESSION_TOKEN; preserved token; explicit source-resume after diagnosis"
      (( rc != 0 )) || rc=4
    fi
  fi
  if [[ "$V11_BACKUP_OK" != true && -n "$V11_BACKUP_DIR" ]]; then
    relay_exec "touch '$V11_BACKUP_DIR/.inprogress'" >/dev/null 2>&1 || true
  fi
  if [[ "$V11_ABORTED" != true && -n "$V11_COPY_LOCK" && -f "$V11_COPY_LOCK/owner" ]] &&
     [[ $(head -n 1 "$V11_COPY_LOCK/owner") == "$SESSION_TOKEN|$$" ]]; then
    rm -f -- "$V11_COPY_LOCK/owner"
    rmdir -- "$V11_COPY_LOCK" || { (( rc != 0 )) || rc=5; }
  fi
  # Only this invocation's mktemp directory is disposable; rollback/image dirs stay.
  if [[ -n "$V11_SCRATCH" && -d "$V11_SCRATCH" && "$V11_SCRATCH" == "${TMPDIR:-/tmp}"/green-v11.* ]]; then rm -rf -- "$V11_SCRATCH"; fi
  exit "$rc"
}
v11_claim_copy(){
  local lock
  if [[ "$SOURCE_ROOT" == / ]]; then lock="${GREEN_V11_COPY_LOCK_ROOT:-/run/green-v11-copy}"
  else lock="$SOURCE_ROOT/.green-v11-copy"; fi
  v11_path "$lock"
  [[ ! -L "$lock" ]] || v11_fail 'copy lock is a symlink'
  (umask 077; mkdir -- "$lock") 2>/dev/null || v11_fail 'source copy lock exists; inspect exact owner, do not clear blindly'
  V11_COPY_LOCK="$lock"
  printf '%s\n' "$SESSION_TOKEN|$$" > "$lock/owner"
  printf 'host=%s\n' "$(uname -n)" >> "$lock/owner"
}
v11_run_backup(){
  pre_basic_relay_checks
  v11_claim_copy
  v11_backup_preflight
  resolve_items_to_file "$V11_SCRATCH/source-preflight-items.txt"
  v11_python source-preflight "$SOURCE_ROOT" --items-file "$V11_SCRATCH/source-preflight-items.txt" --exclude-file "$EXCLUDE_FILE"
  if [[ "$SOURCE_SERVICE_CYCLE" == true ]]; then
    V11_FROZEN=true
    bash "$SCRIPT_DIR/green_v1_services.sh" source-freeze "$SESSION_TOKEN" --yes
    bash "$SCRIPT_DIR/green_v1_services.sh" source-transfer-begin "$SESSION_TOKEN" "$$"
    V11_LEASE=true
  fi
  # Recheck metadata in the actual quiet window as well as before any stop.
  v11_python source-preflight "$SOURCE_ROOT" --items-file "$V11_SCRATCH/source-preflight-items.txt" --exclude-file "$EXCLUDE_FILE"
  backup_to_relay_action
  V11_BACKUP_OK=true
}
v11_restore_args(){
  [[ -n "$TARGET_ID" && -n "$IMAGE_SOURCE_ID" && -n "$IMAGE_ARCH" && -n "$IMAGE_OS_ID" && -n "$IMAGE_OS_VERSION" && -n "$IMAGE_PRODUCT" && -n "$IMAGE_VERSION" ]] || v11_fail 'restore requires explicit source/target identity and compatible arch/OS/product/version'
  [[ -f "$ALLOWLIST" && ! -L "$ALLOWLIST" ]] || v11_fail 'restore requires reviewed allowlist file'
  V11_RESTORE_ARGS=(--target-root "$TARGET_ROOT" --state-dir "$RESTORE_STATE" --token "$SESSION_TOKEN" --target-id "$TARGET_ID" --source-id "$IMAGE_SOURCE_ID" --arch "$IMAGE_ARCH" --os-id "$IMAGE_OS_ID" --os-version "$IMAGE_OS_VERSION" --product "$IMAGE_PRODUCT" --version "$IMAGE_VERSION" --restore-mode "$RESTORE_MODE" --target-mode "$TARGET_MODE" --allowlist "$ALLOWLIST")
  if [[ "$TARGET_SERVICE_CYCLE" == true ]]; then V11_RESTORE_ARGS+=(--service-cycle --services-helper "$SCRIPT_DIR/green_v1_services.sh"); fi
  if [[ "$USE_IMAGE_SERVICES" == true ]]; then V11_RESTORE_ARGS+=(--use-image-services); fi
}
v11_restore_local(){
  local op="$1" image="$IMAGE_DIR"
  v11_restore_args
  [[ "$op" != recover || -z "$IMAGE_ARCHIVE" ]] || v11_fail 'recovery uses the retained journal; do not unpack an archive'
  if [[ -n "$IMAGE_ARCHIVE" ]]; then
    [[ -z "$image" ]] || v11_fail 'choose directory OR tar image'
    image="$V11_SCRATCH/unpacked"
    v11_python unpack "$IMAGE_ARCHIVE" "$image"
  fi
  [[ -n "$image" ]] || v11_fail 'missing image directory/archive'
  v11_python "$op" "$image" "${V11_RESTORE_ARGS[@]}"
}
v11_prepare_relay(){
  local dir="$RELAY_ROOT/$BACKUP_ID" archive
  if relay_exec "test -d '$dir'"; then v11_relay_image verify "$dir"; return; fi
  [[ "$IMAGE_STORE" != none ]] || v11_fail 'directory missing and archive disabled'
  v11_archive_store_check
  for archive in "$IMAGE_ARCHIVE_ROOT/${BACKUP_ID%%/*}_${BACKUP_ID##*/}.tar" "$IMAGE_ARCHIVE_ROOT/${BACKUP_ID%%/*}_${BACKUP_ID##*/}.tar.gz" "$IMAGE_ARCHIVE_ROOT/$BACKUP_ID.tar"; do
    if relay_exec "test -f '$archive'"; then v11_relay_image unpack "$archive" "$dir"; return; fi
  done
  v11_fail 'image directory and checksum-protected archive not found'
}
v11_restore_relay(){
  local op="$1" dir archive remote_tmp relay_id
  pre_basic_relay_checks
  [[ -n "$BACKUP_ID" ]] || v11_fail 'restore requires backup ID'
  v11_restore_args
  relay_id="$(relay_exec 'cat /etc/machine-id')" || v11_fail 'unable to verify relay identity'
  [[ -n "$relay_id" && "$relay_id" != "$TARGET_ID" ]] || v11_fail 'target and relay identity must differ'
  v11_prepare_relay
  dir="$RELAY_ROOT/$BACKUP_ID"
  IMAGE_DIR="$V11_SCRATCH/image"
  if [[ "$(choose_relay_mode)" == rsync ]]; then
    mkdir "$IMAGE_DIR"
    "$RSYNC_BIN" -aH --numeric-ids --links --rsync-path="$(relay_rsync_path)" -e "$(rsync_ssh_cmd "$RELAY_PASS")" "$RELAY_USER@$RELAY_IP:$dir/" "$IMAGE_DIR/"
  else
    remote_tmp="$(relay_exec "mktemp -d '$V11_RELAY_SCRIPTS/.restore-$SESSION_TOKEN.XXXXXXXX'")"
    [[ "$remote_tmp" == "$V11_RELAY_SCRIPTS/.restore-$SESSION_TOKEN."* ]] || v11_fail 'invalid relay scratch directory'
    v11_path "$remote_tmp"
    archive="$remote_tmp/image.tar"
    v11_relay_image archive "$dir" "$archive" --prefix "$BACKUP_ID"
    scp_cmd "$RELAY_PASS" "$RELAY_USER@$RELAY_IP:$archive" "$V11_SCRATCH/image.tar"
    scp_cmd "$RELAY_PASS" "$RELAY_USER@$RELAY_IP:$archive.sha256" "$V11_SCRATCH/image.tar.sha256"
    v11_python unpack "$V11_SCRATCH/image.tar" "$IMAGE_DIR"
    relay_exec "rm -f '$archive' '$archive.sha256' && rmdir '$remote_tmp/.image.tar.green-archive' && rmdir '$remote_tmp'"
  fi
  v11_python "$op" "$IMAGE_DIR" "${V11_RESTORE_ARGS[@]}"
}
v11_apply_direct(){
  # The old implicit "latest stage" selection could apply the wrong image.
  [[ "$STAGE_ID" =~ ^[0-9]{8}_[0-9]{6}$ ]] || v11_fail 'direct apply requires explicit --stage-id timestamp'
  [[ -n "$TARGET_ID" && -n "$IMAGE_SOURCE_ID" && -n "$IMAGE_ARCH" && -n "$IMAGE_OS_ID" && -n "$IMAGE_OS_VERSION" && -n "$ALLOWLIST" ]] || v11_fail 'direct apply requires the complete target contract'
  local image="$REMOTE_STAGE_ROOT/$STAGE_ID" cmd target_scripts="${GREEN_V11_TARGET_SCRIPT_DIR:-$SCRIPT_DIR}" remote_allow="$ALLOWLIST"
  v11_path "$target_scripts"
  if [[ "$remote_allow" != /* ]]; then
    [[ "$remote_allow" =~ ^[A-Za-z0-9_.-]+$ && "$remote_allow" != . && "$remote_allow" != .. ]] || v11_fail 'unsafe allowlist filename'
    remote_allow="$target_scripts/$remote_allow"
  fi
  cmd="$(v11_quote bash "$target_scripts/green_migration_tool.sh" restore-image --image-dir "$image" --restore-mode "$RESTORE_MODE" --target-root "$TARGET_ROOT" --target-mode "$TARGET_MODE" --target-id "$TARGET_ID" --source-id "$IMAGE_SOURCE_ID" --image-arch "$IMAGE_ARCH" --image-os-id "$IMAGE_OS_ID" --image-os-version "$IMAGE_OS_VERSION" --image-product "$IMAGE_PRODUCT" --image-version "$IMAGE_VERSION" --session-token "$SESSION_TOKEN" --allowlist "$remote_allow" --state-dir "$RESTORE_STATE" --target-service-cycle "$TARGET_SERVICE_CYCLE")"
  [[ "$USE_IMAGE_SERVICES" != true ]] || cmd+=" --use-image-services"
  target_exec "$cmd"
}
v11_stage_direct(){
  pre_basic_direct_checks
  # Reuse the exact cold backup/copy/seal data path with the stage host as relay.
  # This only writes the separate stage directory, never target system files.
  RELAY_IP="$B_IP"; RELAY_USER="$B_USER"; RELAY_PASS="$B_PASS"
  RELAY_ROOT="$(dirname "$REMOTE_STAGE_ROOT")"; BACKUP_NAME="$(basename "$REMOTE_STAGE_ROOT")"
  IMAGE_STORE=none
  V11_RELAY_SCRIPTS="${GREEN_V11_TARGET_SCRIPT_DIR:-$SCRIPT_DIR}"
  v11_path "$V11_RELAY_SCRIPTS"
  v11_run_backup
  log_info "stage 完成。请到目标机查看：$REMOTE_STAGE_ROOT/$TIMESTAMP; STAGE_ID=$TIMESTAMP"
}
v11_list_archives(){
  [[ "$IMAGE_STORE" != none ]] || return 0
  v11_archive_store_check
  relay_exec "find '$IMAGE_ARCHIVE_ROOT' -mindepth 1 -maxdepth 3 -type f -name '*.sha256' -print" | sort
}
main(){
  parse_args "$@"; v11_validate
  V11_SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/green-v11.XXXXXXXX")"
  trap v11_finish EXIT
  trap 'V11_ABORTED=true; exit 130' INT
  trap 'V11_ABORTED=true; exit 143' TERM HUP
  init_helper_bins
  if [[ "$ACTION" == stage || "$ACTION" == apply || "$ACTION" == precheck ]]; then export SSHPASS="$B_PASS"; else export SSHPASS="$RELAY_PASS"; fi
  log_info "任务初始化，日志模式：控制台输出；DEBUG=$GREEN_MIG_DEBUG"
  case "$ACTION" in
    precheck) precheck_action ;;
    stage) v11_stage_direct ;;
    apply) apply_action ;;
    backup-to-relay) v11_run_backup ;;
    list-relay-backups) list_relay_backups_action; v11_list_archives ;;
    list-archived-images) v11_list_archives ;;
    precheck-restore) precheck_restore_action ;;
    restore-from-relay) restore_from_relay_action ;;
    restore-image) v11_restore_local apply ;;
    plan-image) v11_restore_local plan ;;
    recover-image) v11_restore_local recover ;;
    setup-ssh-key-auth) setup_ssh_key_auth_action ;;
    *) v11_fail "unsupported action: $ACTION" ;;
  esac
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then main "$@"; fi
