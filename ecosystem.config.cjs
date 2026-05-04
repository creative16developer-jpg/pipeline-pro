/**
 * PM2 Ecosystem — PipelinePro
 *
 * Start everything:   pm2 start ecosystem.config.cjs
 * Save & auto-start:  pm2 save && pm2 startup
 * Live logs:          pm2 logs
 * Status:             pm2 status
 */

const path = require("path");
const PIPELINE_DIR = path.join(__dirname, "artifacts", "pipeline");

// Python inside the venv — created by:
//   cd artifacts/pipeline && python3 -m venv venv && venv/bin/pip install -r requirements.txt
const PYTHON = path.join(PIPELINE_DIR, "venv", "bin", "python3");

module.exports = {
  apps: [
    // ─────────────────────────────────────────────────────────────
    // 1. FastAPI backend  (also serves the built React frontend)
    // ─────────────────────────────────────────────────────────────
    {
      name: "pipeline-api",
      cwd: PIPELINE_DIR,
      script: PYTHON,
      args: "main.py",
      interpreter: "none",
      env: {
        PORT: "8000",
      },
      watch: false,
      autorestart: true,
      restart_delay: 3000,
      max_restarts: 10,
    },

    // ─────────────────────────────────────────────────────────────
    // 2. Celery worker  (background jobs: fetch, process, pipeline)
    //    Requires Redis — make sure REDIS_URL is set in .env or env
    // ─────────────────────────────────────────────────────────────
    {
      name: "celery-worker",
      cwd: PIPELINE_DIR,
      script: PYTHON,
      args: "-m celery -A celery_app.celery_app worker --loglevel=info",
      interpreter: "none",
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      max_restarts: 10,
    },
  ],
};
