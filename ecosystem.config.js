const VENV = "/home/dev/public_html/venv";
const VENV_BIN = `${VENV}/bin`;
const BASE = "/home/dev/public_html/pipeline-pro";
const PIPELINE = `${BASE}/artifacts/pipeline`;

module.exports = {
  apps: [
    {
      name: "pipeline-api",
      cwd: PIPELINE,
      script: `${VENV_BIN}/python3`,
      args: "main.py",
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        PATH: `${VENV_BIN}:/usr/local/bin:/usr/bin:/bin`,
        VIRTUAL_ENV: VENV,
        PYTHONPATH: PIPELINE
      }
    },
    {
      name: "pipeline-worker",
      cwd: PIPELINE,
      script: "bash",
      args: "start_worker.sh",
      interpreter: "none",
      autorestart: true,
      watch: false,
      env: {
        PATH: `${VENV_BIN}:/usr/local/bin:/usr/bin:/bin`,
        VIRTUAL_ENV: VENV,
        PYTHONPATH: PIPELINE
      }
    },
    {
      name: "dashboard-build",
      cwd: BASE,
      script: "pnpm",
      args: "--filter @workspace/dashboard build",
      interpreter: "none",
      autorestart: false,
      watch: false,
      env: {
        PATH: `/usr/local/bin:/usr/bin:/bin`
      }
    }
  ]
};
