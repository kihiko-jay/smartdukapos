/**
 * Structured logger for the DukaPOS sync agent (v4.0).
 *
 * Changes:
 *  - All logs carry store_id and agent_version as default metadata
 *  - JSON format in production (machine-parseable for CloudWatch, Datadog, etc.)
 *  - Pretty format in development
 *  - Log file rotates at 10MB, keeps 5 files (50MB max)
 *  - Unhandled errors include stack traces
 */

const winston = require("winston");
const path    = require("path");
require("dotenv").config();

const isProd    = process.env.NODE_ENV === "production";
const logFile   = process.env.LOG_FILE || path.join(process.cwd(), "logs", "sync-agent.log");
const storeId   = process.env.STORE_ID   || "unknown";
const agentVer  = process.env.npm_package_version || "unknown";

// ── Formats ───────────────────────────────────────────────────────────────────

const jsonFormat = winston.format.combine(
  winston.format.timestamp(),
  winston.format.errors({ stack: true }),
  winston.format.json()
);

const prettyFormat = winston.format.combine(
  winston.format.colorize(),
  winston.format.timestamp({ format: "HH:mm:ss" }),
  winston.format.errors({ stack: true }),
  winston.format.printf(({ level, message, timestamp, stack, ...meta }) => {
    const metaStr = Object.keys(meta).length > 0
      ? " " + JSON.stringify(meta, null, 0)
      : "";
    return `${timestamp} [${level}] ${message}${metaStr}${stack ? "\n" + stack : ""}`;
  })
);

// ── Logger ────────────────────────────────────────────────────────────────────

const logger = winston.createLogger({
  level:            process.env.LOG_LEVEL || "info",
  defaultMeta:      { store_id: storeId, agent_version: agentVer },
  transports: [
    new winston.transports.Console({
      format: isProd ? jsonFormat : prettyFormat,
    }),
    new winston.transports.File({
      filename: logFile,
      format:   jsonFormat,          // always JSON in file (for log aggregators)
      maxsize:  10 * 1024 * 1024,    // 10MB
      maxFiles: 5,
      tailable: true,
    }),
  ],
  exceptionHandlers: [
    new winston.transports.File({ filename: logFile.replace(".log", "-exceptions.log") }),
  ],
  rejectionHandlers: [
    new winston.transports.File({ filename: logFile.replace(".log", "-rejections.log") }),
  ],
});

module.exports = logger;
