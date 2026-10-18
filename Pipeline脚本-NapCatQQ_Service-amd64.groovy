// ============================================================
// NapCatQQ Service - Jenkins Pipeline
// 功能：拉取代码，构建 linux/amd64 后端与前端镜像并推送到阿里云镜像仓库。
// ============================================================
pipeline {
    agent any

    environment {
        GITHUB_REPO = 'https://github.com/zhinianboke/NapCatQQ_Service.git'
        GITHUB_CREDENTIALS = 'github-token'

        ALIYUN_REGISTRY = 'registry.cn-shanghai.aliyuncs.com'
        ALIYUN_NAMESPACE = 'zhinian-software'
        ALIYUN_CREDENTIALS = 'aliyun-docker-credentials'

        BACKEND_IMAGE = 'napcatqq-service-backend'
        FRONTEND_IMAGE = 'napcatqq-service-frontend'
        BUILD_PLATFORM = 'linux/amd64'
    }

    options {
        timestamps()
        disableConcurrentBuilds()
        skipDefaultCheckout(true)
        timeout(time: 60, unit: 'MINUTES')
    }

    stages {
        stage('拉取代码') {
            steps {
                echo '开始拉取代码...'
                deleteDir()
                sh 'git config --global http.version HTTP/1.1'
                sh 'git config --global http.postBuffer 524288000'
                git branch: 'main',
                    credentialsId: "${GITHUB_CREDENTIALS}",
                    url: "${GITHUB_REPO}"
                echo '代码拉取完成'
            }
        }

        stage('构建并推送后端镜像') {
            steps {
                echo '开始构建后端镜像...'
                script {
                    withCredentials([usernamePassword(
                        credentialsId: "${ALIYUN_CREDENTIALS}",
                        usernameVariable: 'REGISTRY_USER',
                        passwordVariable: 'REGISTRY_PASS'
                    )]) {
                        sh '''
                            set -eu
                            REGISTRY="${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}"
                            VERSION_TAG="${BUILD_NUMBER}"
                            IMAGE="${REGISTRY}/${BACKEND_IMAGE}"

                            echo "${REGISTRY_PASS}" | docker login \
                                "${ALIYUN_REGISTRY}" \
                                -u "${REGISTRY_USER}" \
                                --password-stdin

                            docker build --provenance=false --platform "${BUILD_PLATFORM}" \
                                -t "${IMAGE}:${VERSION_TAG}" \
                                -t "${IMAGE}:latest" \
                                -f backend/Dockerfile \
                                .

                            docker push "${IMAGE}:${VERSION_TAG}"
                            docker push "${IMAGE}:latest"
                        '''
                    }
                }
                echo '后端镜像推送完成'
            }
        }

        stage('构建并推送前端镜像') {
            steps {
                echo '开始构建前端镜像...'
                script {
                    withCredentials([usernamePassword(
                        credentialsId: "${ALIYUN_CREDENTIALS}",
                        usernameVariable: 'REGISTRY_USER',
                        passwordVariable: 'REGISTRY_PASS'
                    )]) {
                        sh '''
                            set -eu
                            REGISTRY="${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}"
                            VERSION_TAG="${BUILD_NUMBER}"
                            IMAGE="${REGISTRY}/${FRONTEND_IMAGE}"

                            echo "${REGISTRY_PASS}" | docker login \
                                "${ALIYUN_REGISTRY}" \
                                -u "${REGISTRY_USER}" \
                                --password-stdin

                            docker build --provenance=false --platform "${BUILD_PLATFORM}" \
                                -t "${IMAGE}:${VERSION_TAG}" \
                                -t "${IMAGE}:latest" \
                                -f frontend/Dockerfile \
                                .

                            docker push "${IMAGE}:${VERSION_TAG}"
                            docker push "${IMAGE}:latest"
                        '''
                    }
                }
                echo '前端镜像推送完成'
            }
        }

        stage('清理本项目镜像') {
            steps {
                sh '''
                    set +e
                    REGISTRY="${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}"
                    docker image rm \
                        "${REGISTRY}/${BACKEND_IMAGE}:${BUILD_NUMBER}" \
                        "${REGISTRY}/${BACKEND_IMAGE}:latest" \
                        "${REGISTRY}/${FRONTEND_IMAGE}:${BUILD_NUMBER}" \
                        "${REGISTRY}/${FRONTEND_IMAGE}:latest" \
                        >/dev/null 2>&1
                    true
                '''
                echo '本项目镜像清理完成'
            }
        }
    }

    post {
        success {
            echo """
            ========================================
            NapCatQQ Service 镜像构建成功
            ========================================
            镜像已推送到阿里云：
              后端: ${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}/${BACKEND_IMAGE}:latest
              前端: ${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}/${FRONTEND_IMAGE}:latest

            生产环境部署命令：
              bash deploy_remote.sh
            ========================================
            """
        }
        failure {
            echo '构建失败，请检查 Jenkins 日志。'
        }
        always {
            sh '''
                set +e
                REGISTRY="${ALIYUN_REGISTRY}/${ALIYUN_NAMESPACE}"
                docker logout "${ALIYUN_REGISTRY}" >/dev/null 2>&1
                docker image rm \
                    "${REGISTRY}/${BACKEND_IMAGE}:${BUILD_NUMBER}" \
                    "${REGISTRY}/${BACKEND_IMAGE}:latest" \
                    "${REGISTRY}/${FRONTEND_IMAGE}:${BUILD_NUMBER}" \
                    "${REGISTRY}/${FRONTEND_IMAGE}:latest" \
                    >/dev/null 2>&1
                true
            '''
            echo 'NapCatQQ Service 构建流程结束。'
        }
    }
}
