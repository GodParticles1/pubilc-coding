// V1.1 candidate. Keep the existing parameterized Job configuration: this file
// deliberately has no partial parameters{} block. See README.md for added opts.
pipeline {
  agent any
  options {
    disableConcurrentBuilds()
    timestamps()
    timeout(time: 120, unit: 'MINUTES')
  }
  environment {
    TOOL_SCRIPT = 'green_migration_tool.sh'
    EXCLUDE_FILE = 'exclude.txt'
  }
  stages {
    stage('validate-release-and-parameters') {
      steps {
        script {
          env.GREEN_MIG_SCRATCH = "${pwd(tmp: true)}/green-v11-${env.BUILD_NUMBER}"
        }
        sh '''#!/usr/bin/env bash
set +x
set -Eeuo pipefail
bash green_v11_jenkins.sh validate
'''
      }
    }
    stage('run-migration') {
      steps {
        script {
          // Bind only credentials relevant to the selected action. A missing
          // unrelated target credential must not break source-only backup.
          def names = []
          if (env.ACTION in ['backup-to-relay', 'stage']) {
            names.add(string(credentialsId: 'green-mig-source-pass', variable: 'SOURCE_PASS'))
          }
          if (!(env.ACTION in ['backup-to-relay', 'list-relay-backups', 'list-archived-images'])) {
            names.add(string(credentialsId: 'green-mig-target-pass', variable: 'TARGET_PASS'))
          }
          if (env.TRANSFER_MODE == 'relay' || env.ACTION in ['backup-to-relay', 'list-relay-backups', 'list-archived-images', 'precheck-restore', 'restore-from-relay']) {
            names.add(string(credentialsId: 'green-mig-relay-pass', variable: 'RELAY_PASS'))
          }
          withCredentials(names) {
            try {
              sh '''#!/usr/bin/env bash
set +x
set -Eeuo pipefail
bash green_v11_jenkins.sh run
'''
            } finally {
              // The Shell helper owns state and compensation. This bounded
              // fallback is idempotent, and a live transfer lease blocks resume.
              timeout(time: 10, unit: 'MINUTES') {
                sh '''#!/usr/bin/env bash
set +x
set -Eeuo pipefail
bash green_v11_jenkins.sh compensate
'''
              }
            }
          }
        }
      }
    }
  }
  // No remote command bodies, raw config.xml, image data or credential files are
  // archived. Controller diagnostics stay in the console without bash -x.
}
