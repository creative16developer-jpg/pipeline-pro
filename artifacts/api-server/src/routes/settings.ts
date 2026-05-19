/**
 * Proxy /api/settings/* → Python FastAPI.
 * API key management for AI providers.
 */
import { Router, type IRouter } from "express";
import { makePythonProxy } from "../lib/python-proxy";

const router: IRouter = Router();
router.all("/{*path}", makePythonProxy("/api/settings"));
export default router;
