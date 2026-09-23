// Jenkinsfile — AURA's small DevOps component.
//
// Deliberately minimal, per the project spec: DevOps is a supporting
// demonstration, not the main project. This pipeline does NOT attempt to
// run the Windows desktop app itself (it needs a real Windows 11 machine
// with a display, microphone, etc.) — it runs the test suite, which is
// the part that's meaningfully CI-able.
//
// Usage: point a Jenkins "Pipeline script from SCM" job at this repo.

pipeline {
    agent any

    stages {
        stage('Checkout') {
            steps {
                checkout scm
            }
        }

        stage('Set up Python environment') {
            steps {
                sh '''
                    python3 -m venv .venv
                    . .venv/bin/activate
                    pip install --upgrade pip
                    pip install -r requirements.txt
                '''
            }
        }

        stage('Run tests') {
            steps {
                sh '''
                    . .venv/bin/activate
                    python -m pytest tests/ -v --junitxml=test-results.xml
                '''
            }
        }
    }

    post {
        always {
            junit 'test-results.xml'
        }
    }
}
