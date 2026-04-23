module.exports = {
  apps: [
    {
      name: "pipeline-api",
      cwd: "/home/dev/public_html/pipeline-pro/artifacts/pipeline",
      script: "python",
      args: "main.py",
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        PORT: "8000"
      }
    },
    {
      name: "pipeline-worker",
      cwd: "/home/dev/public_html/pipeline-pro/artifacts/pipeline",
      script: "bash",
      args: "start_worker.sh",
      interpreter: "none",
      autorestart: true,
      watch: false
    },
    {
      name: "dashboard-build",
      cwd: "/home/dev/public_html/pipeline-pro",
      script: "pnpm",
      args: "--filter @workspace/dashboard build",
      interpreter: "none",
      autorestart: false,
      watch: false
    }
  ]
};
