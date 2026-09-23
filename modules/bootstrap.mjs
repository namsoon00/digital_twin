import { startApplication } from "./shell/startup.mjs";
import { initializeState } from "./state/initialize.mjs";

initializeState();
startApplication();
