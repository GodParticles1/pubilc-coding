// SANITIZED HISTORICAL REFERENCE; NOT SAFE TO RUN: remote command password exposure and unguarded ARM helper distribution. See project README.

pipeline {
  agent any

  environment {
    TOOL_SCRIPT  = 'green_migration_tool.sh'
    EXCLUDE_FILE = 'exclude.txt'
  }

  stages {
    stage('check-local-files') {
      steps {
        sh '''
          set -e
          pwd
          ls -l
          test -f "${TOOL_SCRIPT}"
          test -f "${EXCLUDE_FILE}"
          bash -n "${TOOL_SCRIPT}"
          command -v ssh
          command -v sshpass
          command -v scp
          command -v rsync

          [ -f sshpass ] && { chmod +x sshpass; file sshpass; } || true
          [ -f rsync ] && { chmod +x rsync; file rsync; } || true

          if [ -n "${ITEM_FILE}" ]; then
            test -f "${ITEM_FILE}"
            echo "===== ITEM_FILE ====="
            echo "${ITEM_FILE}"
            cat "${ITEM_FILE}"
          fi
        '''
      }
    }

    stage('show-parameters') {
      steps {
        echo "ACTION=${params.ACTION}"
        echo "TRANSFER_MODE=${params.TRANSFER_MODE}"
        echo "TARGET_ENV=${params.TARGET_ENV}"
        echo "TARGET_IP=${params.TARGET_IP}"
        echo "SOURCE_IP=${params.SOURCE_IP}"
        echo "RELAY_IP=${params.RELAY_IP}"
        echo "RELAY_ROOT=${params.RELAY_ROOT}"
        echo "TARGET_USER=${params.TARGET_USER}"
        echo "SOURCE_USER=${params.SOURCE_USER}"
        echo "RELAY_USER=${params.RELAY_USER}"
        echo "BACKUP_NAME=${params.BACKUP_NAME}"
        echo "BACKUP_ID=${params.BACKUP_ID}"
        echo "SOURCE_ROOT=${params.SOURCE_ROOT}"
        echo "REMOTE_DIR_TARGET=${params.REMOTE_DIR_TARGET}"
        echo "REMOTE_DIR_SOURCE=${params.REMOTE_DIR_SOURCE}"
        echo "RABBIT_USER=${params.RABBIT_USER}"
        echo "RABBIT_VHOST=${params.RABBIT_VHOST}"
        echo "RABBIT_TAGS=${params.RABBIT_TAGS}"
        echo "ITEM_FILE=${params.ITEM_FILE}"
      }
    }

    stage('check-parameters') {
      steps {
        script {
          if (!(params.TRANSFER_MODE in ['relay', 'direct'])) {
            error('TRANSFER_MODE 只支持 relay 或 direct')
          }

          if (params.TRANSFER_MODE == 'direct' && params.TARGET_ENV == 'rainbowC') {
            error('直连模式不支持 rainbowC，请改用 relay 模式')
          }

          if ((params.ACTION == 'backup-to-relay' || params.ACTION == 'stage') && !params.SOURCE_IP?.trim()) {
            error("${params.ACTION} 需要填写 SOURCE_IP")
          }

          if ((params.ACTION == 'precheck-restore' ||
               params.ACTION == 'restore-from-relay' ||
               params.ACTION == 'list-relay-backups' ||
               params.ACTION == 'backup-to-relay' ||
               params.ACTION == 'setup-ssh-key-auth') && !params.RELAY_IP?.trim()) {
            error("${params.ACTION} 需要填写 RELAY_IP")
          }

          if ((params.ACTION == 'precheck-restore' ||
               params.ACTION == 'restore-from-relay' ||
               params.ACTION == 'list-relay-backups' ||
               params.ACTION == 'backup-to-relay') && !params.RELAY_ROOT?.trim()) {
            error("${params.ACTION} 需要填写 RELAY_ROOT")
          }

          if (params.ACTION == 'backup-to-relay' && !params.BACKUP_NAME?.trim()) {
            error('backup-to-relay 需要填写 BACKUP_NAME')
          }

          if ((params.ACTION == 'precheck-restore' || params.ACTION == 'restore-from-relay') && !params.BACKUP_ID?.trim()) {
            error("${params.ACTION} 需要填写 BACKUP_ID")
          }

          if (params.ITEM_FILE?.trim()) {
            if (!fileExists(params.ITEM_FILE)) {
              error("ITEM_FILE 不存在：${params.ITEM_FILE}")
            }
          }
        }
      }
    }

    stage('push-helper-binaries-to-rainbow-if-needed') {
      when {
        expression { params.TARGET_ENV == 'rainbowC' }
      }
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS')
        ]) {
          sh '''
            set -e

            sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "mkdir -p '${REMOTE_DIR_TARGET}'"

            TARGET_CHECK_OUTPUT="$(sshpass -p "${TARGET_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "
                if command -v sshpass >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/sshpass' ]; then
                  echo HAVE_SSHPASS=1
                else
                  echo HAVE_SSHPASS=0
                fi

                if command -v rsync >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/rsync' ]; then
                  echo HAVE_RSYNC=1
                else
                  echo HAVE_RSYNC=0
                fi
              ")"

            echo "$TARGET_CHECK_OUTPUT"

            HAVE_SSHPASS="$(echo "$TARGET_CHECK_OUTPUT" | awk -F= '/HAVE_SSHPASS=/{print $2}' | tail -1)"
            HAVE_RSYNC="$(echo "$TARGET_CHECK_OUTPUT" | awk -F= '/HAVE_RSYNC=/{print $2}' | tail -1)"

            if [ "$HAVE_SSHPASS" = "0" ]; then
              echo "彩虹目标机缺少 sshpass，开始下发"
              test -f sshpass
              chmod +x sshpass
              sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                sshpass "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/sshpass"
            else
              echo "彩虹目标机已有 sshpass，跳过下发"
            fi

            if [ "$HAVE_RSYNC" = "0" ]; then
              echo "彩虹目标机缺少 rsync，开始下发"
              test -f rsync
              chmod +x rsync
              sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                rsync "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/rsync"
            else
              echo "彩虹目标机已有 rsync，跳过下发"
            fi

            sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "
                [ -x '${REMOTE_DIR_TARGET}/sshpass' ] && chmod +x '${REMOTE_DIR_TARGET}/sshpass' || true
                [ -x '${REMOTE_DIR_TARGET}/rsync' ] && chmod +x '${REMOTE_DIR_TARGET}/rsync' || true
                command -v sshpass 2>/dev/null || true
                command -v rsync 2>/dev/null || true
                file '${REMOTE_DIR_TARGET}/sshpass' 2>/dev/null || true
                file '${REMOTE_DIR_TARGET}/rsync' 2>/dev/null || true
              "
          '''
        }
      }
    }

    stage('setup-ssh-jenkins-to-relay') {
      when {
        expression { params.ACTION == 'setup-ssh-key-auth' && params.TRANSFER_MODE == 'relay' }
      }
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-relay-pass', variable: 'RELAY_PASS')
        ]) {
          sh '''
            set -e
            chmod +x "${TOOL_SCRIPT}"

            bash "${TOOL_SCRIPT}" setup-ssh-key-auth \
              -b "${RELAY_IP}" \
              -u "${RELAY_USER}" \
              -p "${RELAY_PASS}" \
              --exclude-file "${EXCLUDE_FILE}"
          '''
        }
      }
    }

    stage('setup-ssh-jenkins-to-target') {
      when {
        expression { params.ACTION == 'setup-ssh-key-auth' }
      }
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS')
        ]) {
          sh '''
            set -e
            chmod +x "${TOOL_SCRIPT}"

            bash "${TOOL_SCRIPT}" setup-ssh-key-auth \
              -b "${TARGET_IP}" \
              -u "${TARGET_USER}" \
              -p "${TARGET_PASS}" \
              --exclude-file "${EXCLUDE_FILE}"
          '''
        }
      }
    }

    stage('setup-ssh-target-to-relay') {
      when {
        expression { params.ACTION == 'setup-ssh-key-auth' && params.TRANSFER_MODE == 'relay' }
      }
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS'),
          string(credentialsId: 'green-mig-relay-pass', variable: 'RELAY_PASS')
        ]) {
          sh '''
            set -e

            NEED_ARM_HELPERS="false"
            if [ "${TARGET_ENV}" = "rainbowC" ]; then
              NEED_ARM_HELPERS="true"
            fi

            sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "mkdir -p '${REMOTE_DIR_TARGET}'"

            sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TOOL_SCRIPT}" "${EXCLUDE_FILE}" \
              "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/"

            REMOTE_HAS_SSHPASS="false"
            REMOTE_HAS_RSYNC="false"

            if sshpass -p "${TARGET_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "command -v sshpass >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/sshpass' ]"; then
              REMOTE_HAS_SSHPASS="true"
            fi

            if sshpass -p "${TARGET_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "command -v rsync >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/rsync' ]"; then
              REMOTE_HAS_RSYNC="true"
            fi

            echo "NEED_ARM_HELPERS=${NEED_ARM_HELPERS}"
            echo "REMOTE_HAS_SSHPASS=${REMOTE_HAS_SSHPASS}"
            echo "REMOTE_HAS_RSYNC=${REMOTE_HAS_RSYNC}"

            if [ "${NEED_ARM_HELPERS}" = "true" ] && [ "${REMOTE_HAS_SSHPASS}" != "true" ]; then
              test -f sshpass
              chmod +x sshpass
              sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                sshpass "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/sshpass"
            fi

            if [ "${NEED_ARM_HELPERS}" = "true" ] && [ "${REMOTE_HAS_RSYNC}" != "true" ]; then
              test -f rsync
              chmod +x rsync
              sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                rsync "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/rsync"
            fi

            sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "
                cd '${REMOTE_DIR_TARGET}' && \
                chmod +x '${TOOL_SCRIPT}' && \
                [ -x '${REMOTE_DIR_TARGET}/sshpass' ] && chmod +x '${REMOTE_DIR_TARGET}/sshpass' || true && \
                [ -x '${REMOTE_DIR_TARGET}/rsync' ] && chmod +x '${REMOTE_DIR_TARGET}/rsync' || true && \
                export PATH='${REMOTE_DIR_TARGET}':\\$PATH && \
                bash '${TOOL_SCRIPT}' setup-ssh-key-auth \
                  -b '${RELAY_IP}' \
                  -u '${RELAY_USER}' \
                  -p '${RELAY_PASS}' \
                  --exclude-file '${EXCLUDE_FILE}'
              "

            sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "
                export PATH='${REMOTE_DIR_TARGET}':\\$PATH && \
                ssh -o BatchMode=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null ${RELAY_USER}@${RELAY_IP} 'echo target_to_relay_ok'
              "
          '''
        }
      }
    }

    stage('run-action') {
      when {
        expression { params.ACTION != 'setup-ssh-key-auth' }
      }
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS'),
          string(credentialsId: 'green-mig-relay-pass', variable: 'RELAY_PASS'),
          string(credentialsId: 'green-mig-source-pass', variable: 'SOURCE_PASS')
        ]) {
          sh '''
            set -e

            export GREEN_MIG_B_PASS="${TARGET_PASS}"
            export GREEN_MIG_RELAY_PASS="${RELAY_PASS}"
            export GREEN_MIG_SOURCE_PASS="${SOURCE_PASS}"
            export GREEN_MIG_RABBIT_PASS="REDACTED_MUST_USE_JENKINS_CREDENTIAL"

            chmod +x "${TOOL_SCRIPT}"

            if [ "${ACTION}" = "list-relay-backups" ]; then
              bash "${TOOL_SCRIPT}" list-relay-backups \
                --relay-ip "${RELAY_IP}" \
                --relay-user "${RELAY_USER}" \
                --relay-root "${RELAY_ROOT}" \
                --exclude-file "${EXCLUDE_FILE}"

            elif [ "${ACTION}" = "backup-to-relay" ]; then
              test -n "${SOURCE_IP}"
              test -n "${BACKUP_NAME}"

              ITEM_FILE_ARGS=""
              ITEM_FILE_SCP=""
              if [ -n "${ITEM_FILE}" ]; then
                ITEM_FILE_BASE="$(basename "${ITEM_FILE}")"
                ITEM_FILE_ARGS="--item-file '${REMOTE_DIR_SOURCE}/${ITEM_FILE_BASE}'"
                ITEM_FILE_SCP="${ITEM_FILE}"
              fi

              sshpass -p "${SOURCE_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${SOURCE_USER}@${SOURCE_IP}" "mkdir -p '${REMOTE_DIR_SOURCE}'"

              NEED_ARM_HELPERS="false"
              if [ "${TARGET_ENV}" = "rainbowC" ]; then
                NEED_ARM_HELPERS="true"
              fi

              REMOTE_HAS_RSYNC="false"
              if sshpass -p "${SOURCE_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${SOURCE_USER}@${SOURCE_IP}" "command -v rsync >/dev/null 2>&1 || [ -x '${REMOTE_DIR_SOURCE}/rsync' ]"; then
                REMOTE_HAS_RSYNC="true"
              fi

              if [ -n "${ITEM_FILE_SCP}" ]; then
                sshpass -p "${SOURCE_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                  "${TOOL_SCRIPT}" "${EXCLUDE_FILE}" "${ITEM_FILE_SCP}" \
                  "${SOURCE_USER}@${SOURCE_IP}:${REMOTE_DIR_SOURCE}/"
              else
                sshpass -p "${SOURCE_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                  "${TOOL_SCRIPT}" "${EXCLUDE_FILE}" \
                  "${SOURCE_USER}@${SOURCE_IP}:${REMOTE_DIR_SOURCE}/"
              fi

              if [ "${NEED_ARM_HELPERS}" = "true" ] && [ "${REMOTE_HAS_RSYNC}" != "true" ]; then
                sshpass -p "${SOURCE_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                  rsync "${SOURCE_USER}@${SOURCE_IP}:${REMOTE_DIR_SOURCE}/rsync"
              fi

              REMOTE_CMD=$(cat <<EOF
export GREEN_MIG_RELAY_PASS='${RELAY_PASS}'
export GREEN_MIG_SOURCE_PASS='${SOURCE_PASS}'
export PATH='${REMOTE_DIR_SOURCE}':\$PATH
cd '${REMOTE_DIR_SOURCE}' && \
chmod +x '${TOOL_SCRIPT}' && \
[ -x '${REMOTE_DIR_SOURCE}/rsync' ] && chmod +x '${REMOTE_DIR_SOURCE}/rsync' || true && \
bash '${TOOL_SCRIPT}' backup-to-relay \
  --relay-ip '${RELAY_IP}' \
  --relay-user '${RELAY_USER}' \
  --relay-root '${RELAY_ROOT}' \
  --backup-name '${BACKUP_NAME}' \
  --source-root '${SOURCE_ROOT}' \
  --exclude-file '${EXCLUDE_FILE}' \
  ${ITEM_FILE_ARGS} \
  --rabbit-user '${RABBIT_USER}' \
  --rabbit-vhost '${RABBIT_VHOST}' \
  --rabbit-tags '${RABBIT_TAGS}'
EOF
)
              printf '%s
' "$REMOTE_CMD" > remote_cmd_backup_to_relay.txt
              echo "===== remote backup-to-relay command ====="
              cat remote_cmd_backup_to_relay.txt

              sshpass -p "${SOURCE_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${SOURCE_USER}@${SOURCE_IP}" "$REMOTE_CMD"

            elif [ "${ACTION}" = "precheck-restore" ]; then
              sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "mkdir -p '${REMOTE_DIR_TARGET}'"

              NEED_ARM_HELPERS="false"
              if [ "${TARGET_ENV}" = "rainbowC" ]; then
                NEED_ARM_HELPERS="true"
              fi

              REMOTE_HAS_SSHPASS="false"
              REMOTE_HAS_RSYNC="false"
              if sshpass -p "${TARGET_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "command -v sshpass >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/sshpass' ]"; then
                REMOTE_HAS_SSHPASS="true"
              fi
              if sshpass -p "${TARGET_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "command -v rsync >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/rsync' ]"; then
                REMOTE_HAS_RSYNC="true"
              fi

              sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TOOL_SCRIPT}" "${EXCLUDE_FILE}" \
                "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/"

              if [ "${NEED_ARM_HELPERS}" = "true" ] && [ "${REMOTE_HAS_SSHPASS}" != "true" ]; then
                sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                  sshpass "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/sshpass"
              fi
              if [ "${NEED_ARM_HELPERS}" = "true" ] && [ "${REMOTE_HAS_RSYNC}" != "true" ]; then
                sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                  rsync "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/rsync"
              fi

              REMOTE_CMD=$(cat <<EOF
export GREEN_MIG_B_PASS='${TARGET_PASS}'
export GREEN_MIG_RELAY_PASS='${RELAY_PASS}'
export GREEN_MIG_RABBIT_PASS='REDACTED_MUST_USE_JENKINS_CREDENTIAL'
export PATH='${REMOTE_DIR_TARGET}':\$PATH
cd '${REMOTE_DIR_TARGET}' && \
chmod +x '${TOOL_SCRIPT}' && \
[ -x '${REMOTE_DIR_TARGET}/sshpass' ] && chmod +x '${REMOTE_DIR_TARGET}/sshpass' || true && \
[ -x '${REMOTE_DIR_TARGET}/rsync' ] && chmod +x '${REMOTE_DIR_TARGET}/rsync' || true && \
bash -x '${TOOL_SCRIPT}' precheck-restore \
  --relay-ip '${RELAY_IP}' \
  --relay-user '${RELAY_USER}' \
  --relay-root '${RELAY_ROOT}' \
  --backup-id '${BACKUP_ID}' \
  --exclude-file '${EXCLUDE_FILE}'
EOF
)
              printf '%s\n' "$REMOTE_CMD" > remote_cmd_precheck_restore.txt
              echo "===== remote precheck-restore command ====="
              cat remote_cmd_precheck_restore.txt

              sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "$REMOTE_CMD"

            elif [ "${ACTION}" = "restore-from-relay" ]; then
              sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "mkdir -p '${REMOTE_DIR_TARGET}'"

              NEED_ARM_HELPERS="false"
              if [ "${TARGET_ENV}" = "rainbowC" ]; then
                NEED_ARM_HELPERS="true"
              fi

              REMOTE_HAS_SSHPASS="false"
              REMOTE_HAS_RSYNC="false"
              if sshpass -p "${TARGET_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "command -v sshpass >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/sshpass' ]"; then
                REMOTE_HAS_SSHPASS="true"
              fi
              if sshpass -p "${TARGET_PASS}" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "command -v rsync >/dev/null 2>&1 || [ -x '${REMOTE_DIR_TARGET}/rsync' ]"; then
                REMOTE_HAS_RSYNC="true"
              fi

              sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TOOL_SCRIPT}" "${EXCLUDE_FILE}" \
                "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/"

              if [ "${NEED_ARM_HELPERS}" = "true" ] && [ "${REMOTE_HAS_SSHPASS}" != "true" ]; then
                sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                  sshpass "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/sshpass"
              fi
              if [ "${NEED_ARM_HELPERS}" = "true" ] && [ "${REMOTE_HAS_RSYNC}" != "true" ]; then
                sshpass -p "${TARGET_PASS}" scp -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                  rsync "${TARGET_USER}@${TARGET_IP}:${REMOTE_DIR_TARGET}/rsync"
              fi

              REMOTE_CMD=$(cat <<EOF
export GREEN_MIG_B_PASS='${TARGET_PASS}'
export GREEN_MIG_RELAY_PASS='${RELAY_PASS}'
export GREEN_MIG_RABBIT_PASS='REDACTED_MUST_USE_JENKINS_CREDENTIAL'
export PATH='${REMOTE_DIR_TARGET}':\$PATH
cd '${REMOTE_DIR_TARGET}' && \
chmod +x '${TOOL_SCRIPT}' && \
[ -x '${REMOTE_DIR_TARGET}/sshpass' ] && chmod +x '${REMOTE_DIR_TARGET}/sshpass' || true && \
[ -x '${REMOTE_DIR_TARGET}/rsync' ] && chmod +x '${REMOTE_DIR_TARGET}/rsync' || true && \
bash -x '${TOOL_SCRIPT}' restore-from-relay \
  --relay-ip '${RELAY_IP}' \
  --relay-user '${RELAY_USER}' \
  --relay-root '${RELAY_ROOT}' \
  --backup-id '${BACKUP_ID}' \
  --exclude-file '${EXCLUDE_FILE}' \
  --rabbit-user '${RABBIT_USER}' \
  --rabbit-vhost '${RABBIT_VHOST}' \
  --rabbit-tags '${RABBIT_TAGS}'
EOF
)
              printf '%s\n' "$REMOTE_CMD" > remote_cmd_restore.txt
              echo "===== remote restore command ====="
              cat remote_cmd_restore.txt

              sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "$REMOTE_CMD"

            else
              echo "不支持的 ACTION: ${ACTION}"
              exit 1
            fi
          '''
        }
      }
    }

    stage('post-restore-service-fix') {
      when {
        expression { params.ACTION?.trim()?.toLowerCase()?.replaceAll(/[\s_-]+/, '-') == 'restore-from-relay' }
      }
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS')
        ]) {
          sh '''
            set -e

            REMOTE_CMD=$(cat <<EOF
echo "[SERVICE_FIX] 修复 mysql 数据目录权限"
if grep -q '^mysql:' /etc/passwd 2>/dev/null && [ -d /opt/data/mysql ]; then
  chown -R mysql:mysql /opt/data/mysql || true
  find /opt/data/mysql -type d -exec chmod 755 {} \\\\; || true
  find /opt/data/mysql -type f -exec chmod 644 {} \\\\; || true
  chmod 660 /opt/data/mysql/aria_log_control 2>/dev/null || true
  chmod 660 /opt/data/mysql/ibdata1 2>/dev/null || true
  chmod 660 /opt/data/mysql/ib_logfile* 2>/dev/null || true
fi

echo "[SERVICE_FIX] 启动 mysqld"
/etc/init.d/mysqld start 2>/dev/null || true

echo "[SERVICE_FIX] 启动 rabbitmq"
/etc/init.d/rabbitmq-server start 2>/dev/null || true

echo "[SERVICE_FIX] 重建 rabbitmq gj 用户"
if command -v rabbitmqctl >/dev/null 2>&1; then
  rabbitmqctl delete_user gj >/dev/null 2>&1 || true
  rabbitmqctl add_user gj REDACTED_MUST_USE_JENKINS_CREDENTIAL >/dev/null 2>&1 || true
  rabbitmqctl set_user_tags gj administrator >/dev/null 2>&1 || true
  rabbitmqctl set_permissions gj '.*' '.*' '.*' >/dev/null 2>&1 || true
  rabbitmqctl set_permissions -p / gj '.*' '.*' '.*' >/dev/null 2>&1 || true
fi

echo "[SERVICE_FIX] 启动 monit"
if [ -x /etc/init.d/monit ]; then
  /etc/init.d/monit start 2>/dev/null || true
fi

echo "[SERVICE_FIX] systemctl daemon-reload"
systemctl daemon-reload 2>/dev/null || true

echo "[SERVICE_FIX] monit start all"
monit start all 2>/dev/null || true
EOF
)
            printf '%s\n' "$REMOTE_CMD" > remote_cmd_service_fix.txt
            echo "===== remote service-fix command ====="
            cat remote_cmd_service_fix.txt

            sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "$REMOTE_CMD"
          '''
        }
      }
    }

    stage('post-restore-verify') {
      when {
        expression { params.ACTION == 'restore-from-relay' }
      }
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS')
        ]) {
          sh '''
            set -e

            REMOTE_CMD=$(cat <<EOF
echo "[VERIFY] 关键目录"
ls -ld /opt/data /opt/log /opt/license /opt/REDACTED_MUST_USE_JENKINS_CREDENTIAL /usr/local/nginx 2>/dev/null || true

echo "[VERIFY] mysql"
ps -ef | grep mysqld | grep -v grep || true
ls -l /var/lib/mysql/mysql.sock /tmp/mysql.sock 2>/dev/null || true

echo "[VERIFY] rabbitmq"
rabbitmqctl list_users 2>/dev/null || true
rabbitmqctl list_permissions -p / 2>/dev/null || true
ps -ef | grep -E 'rabbitmq|beam.smp' | grep -v grep || true

echo "[VERIFY] monit"
monit summary 2>/dev/null || true

echo "[VERIFY] nginx"
[ -x /usr/local/nginx/sbin/nginx ] && /usr/local/nginx/sbin/nginx -t || true
EOF
)
            printf '%s\n' "$REMOTE_CMD" > remote_cmd_verify.txt
            echo "===== remote verify command ====="
            cat remote_cmd_verify.txt

            sshpass -p "${TARGET_PASS}" ssh -tt -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
              "${TARGET_USER}@${TARGET_IP}" "$REMOTE_CMD"
          '''
        }
      }
    }

    stage('collect-logs') {
      steps {
        withCredentials([
          string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS'),
          string(credentialsId: 'green-mig-source-pass', variable: 'SOURCE_PASS')
        ]) {
          sh '''
            set +e
            echo "===== local workspace ====="
            pwd
            ls -l
            echo "===== local migration logs ====="
            cat green_migration_*.log 2>/dev/null || true

            if [ "${ACTION}" = "backup-to-relay" ]; then
              sshpass -p "$SOURCE_PASS" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${SOURCE_USER}@${SOURCE_IP}" "
                  echo '===== source logs ====='
                  cd '${REMOTE_DIR_SOURCE}' 2>/dev/null || true
                  ls -l 2>/dev/null || true
                  cat green_migration_*.log 2>/dev/null || true
                " | tee remote_source_output.txt
            fi

            if [ "${ACTION}" = "precheck-restore" ] || [ "${ACTION}" = "restore-from-relay" ] || [ "${ACTION}" = "setup-ssh-key-auth" ]; then
              sshpass -p "$TARGET_PASS" ssh -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null \
                "${TARGET_USER}@${TARGET_IP}" "
                  echo '===== target logs ====='
                  cd '${REMOTE_DIR_TARGET}' 2>/dev/null || true
                  ls -l 2>/dev/null || true
                  cat green_migration_*.log 2>/dev/null || true
                " | tee remote_target_output.txt
            fi
          '''
        }
      }
    }
  }

  post {
    always {
      archiveArtifacts artifacts: 'remote_cmd_*.txt,remote_source_output.txt,remote_target_output.txt,green_migration_*.log', allowEmptyArchive: true
    }
  }
}