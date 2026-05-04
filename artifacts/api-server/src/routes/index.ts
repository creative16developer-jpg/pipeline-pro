import { Router, type IRouter } from "express";
import healthRouter from "./health";
import storesRouter from "./stores";
import productsRouter from "./products";
import jobsRouter from "./jobs";
import sunskyRouter from "./sunsky";
import dashboardRouter from "./dashboard";
import pipelinesRouter from "./pipelines";

const router: IRouter = Router();

router.use(healthRouter);
router.use("/stores", storesRouter);
router.use("/products", productsRouter);
router.use("/jobs", jobsRouter);
router.use("/sunsky", sunskyRouter);
router.use("/dashboard", dashboardRouter);
router.use("/pipelines", pipelinesRouter);

export default router;
