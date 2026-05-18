/**
 * Generic HTTP proxy helper — forwards requests to the Python FastAPI backend.
 * Handles JSON bodies, query strings, multipart/form-data passthrough, and
 * streaming responses (chunked transfer).
 */
import http from "node:http";
import type { Request, Response } from "express";

const PYTHON_HOST = "127.0.0.1";
const PYTHON_PORT = 8000;

/**
 * Build a proxy handler for the given API prefix.
 *
 * @param prefix — the full path prefix as seen by Python, e.g. "/api/csv"
 */
export function makePythonProxy(prefix: string) {
  return function proxy(req: Request, res: Response): void {
    const suffix = req.path === "/" ? "" : req.path;
    const qs = req.url.includes("?") ? "?" + req.url.split("?")[1] : "";
    const target = `${prefix}${suffix}${qs}`;

    const contentType: string = (req.headers["content-type"] ?? "") as string;
    const isMultipart = contentType.startsWith("multipart/form-data");

    // For multipart uploads, pipe the raw request body; don't re-encode it.
    const headers: http.OutgoingHttpHeaders = {
      ...req.headers,
      host: `${PYTHON_HOST}:${PYTHON_PORT}`,
    };

    if (!isMultipart) {
      // Remove transfer-encoding so we can set Content-Length explicitly
      delete headers["transfer-encoding"];
    }

    const options: http.RequestOptions = {
      hostname: PYTHON_HOST,
      port: PYTHON_PORT,
      path: target,
      method: req.method,
      headers,
    };

    const proxyReq = http.request(options, (proxyRes) => {
      res.writeHead(proxyRes.statusCode ?? 502, proxyRes.headers);
      proxyRes.pipe(res, { end: true });
    });

    proxyReq.on("error", (err) => {
      req.log?.error({ err, target }, "Python proxy error");
      if (!res.headersSent) {
        res.status(502).json({ error: "Python service unavailable" });
      }
    });

    if (isMultipart) {
      // Pipe raw incoming stream straight through
      req.pipe(proxyReq, { end: true });
    } else if (req.body && ["POST", "PUT", "PATCH"].includes(req.method)) {
      const body = JSON.stringify(req.body);
      proxyReq.setHeader("Content-Type", "application/json");
      proxyReq.setHeader("Content-Length", Buffer.byteLength(body));
      proxyReq.write(body);
      proxyReq.end();
    } else {
      proxyReq.end();
    }
  };
}
