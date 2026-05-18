/**
 * Proxy /api/generate/* → Python FastAPI.
 * Content generation config, preview, and run endpoints.
 */
import { Router, type IRouter } from "express";
import { makePythonProxy } from "../lib/python-proxy";

const router: IRouter = Router();
router.all("/{*path}", makePythonProxy("/api/generate"));
export default router;
