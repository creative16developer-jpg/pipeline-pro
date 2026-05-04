/**
 * Proxy all /api/pipelines/* requests to the Python FastAPI server on port 8000.
 * The Python server owns all pipeline business logic.
 */
import { Router, type IRouter, type Request, type Response } from "express";
import http from "node:http";

const router: IRouter = Router();

const PYTHON_HOST = "127.0.0.1";
const PYTHON_PORT = 8000;

function proxy(req: Request, res: Response) {
  const target = `/api/pipelines${req.path === "/" ? "" : req.path}${req.url.includes("?") ? "?" + req.url.split("?")[1] : ""}`;

  const options: http.RequestOptions = {
    hostname: PYTHON_HOST,
    port: PYTHON_PORT,
    path: target,
    method: req.method,
    headers: {
      ...req.headers,
      host: `${PYTHON_HOST}:${PYTHON_PORT}`,
    },
  };

  const proxyReq = http.request(options, (proxyRes) => {
    res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
    proxyRes.pipe(res, { end: true });
  });

  proxyReq.on("error", (err) => {
    req.log?.error({ err }, "Pipeline proxy error");
    if (!res.headersSent) {
      res.status(502).json({ error: "Pipeline service unavailable" });
    }
  });

  if (req.body && ["POST", "PUT", "PATCH"].includes(req.method)) {
    const body = JSON.stringify(req.body);
    proxyReq.setHeader("Content-Type", "application/json");
    proxyReq.setHeader("Content-Length", Buffer.byteLength(body));
    proxyReq.write(body);
  }

  proxyReq.end();
}

router.all("/{*path}", proxy);

export default router;
