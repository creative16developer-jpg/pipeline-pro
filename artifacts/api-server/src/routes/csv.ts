/**
 * Proxy /api/csv/* → Python FastAPI.
 * Handles multipart CSV uploads and JSON management endpoints.
 */
import { Router, type IRouter } from "express";
import { makePythonProxy } from "../lib/python-proxy";

const router: IRouter = Router();
router.all("/{*path}", makePythonProxy("/api/csv"));
export default router;
