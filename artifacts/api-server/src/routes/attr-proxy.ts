/**
 * Proxy /api/attr-rules, /api/attr-profiles, and /api/stores/:id/inventory-mapping
 * to the Python FastAPI server on port 8000.
 */
import { Router, type IRouter, type Request, type Response } from "express";
import http from "node:http";

const PYTHON_HOST = "127.0.0.1";
const PYTHON_PORT = 8000;

function makePythonProxy(basePath: string) {
  return function proxy(req: Request, res: Response) {
    const qs = req.url.includes("?") ? "?" + req.url.split("?")[1] : "";
    const target = `${basePath}${req.path === "/" ? "" : req.path}${qs}`;

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
      req.log?.error({ err }, `Python proxy error (${basePath})`);
      if (!res.headersSent) {
        res.status(502).json({ error: "Attribute service unavailable" });
      }
    });

    if (req.body && ["POST", "PUT", "PATCH"].includes(req.method)) {
      const body = JSON.stringify(req.body);
      proxyReq.setHeader("Content-Type", "application/json");
      proxyReq.setHeader("Content-Length", Buffer.byteLength(body));
      proxyReq.write(body);
    }

    proxyReq.end();
  };
}

export const attrRulesRouter: IRouter = Router();
attrRulesRouter.all("/{*path}", makePythonProxy("/api/attr-rules"));

export const attrProfilesRouter: IRouter = Router();
attrProfilesRouter.all("/{*path}", makePythonProxy("/api/attr-profiles"));
