/**
 * Cloud API client for the sync agent.
 * All outbound HTTP calls go through here.
 * Retry logic: exponential backoff, max 3 attempts.
 */
const axios      = require("axios");
const axiosRetry = require("axios-retry").default;
const logger     = require("./logger");
require("dotenv").config();

const { version } = require("../package.json");

const client = axios.create({
  baseURL: process.env.CLOUD_API_URL || "http://localhost:8000/api/v1",
  timeout: 15000,
  headers: {
    "Content-Type": "application/json",
    "X-Sync-Agent": `dukapos-sync/${version}`,
    "X-Store-Id":   process.env.STORE_ID || "1",
  },
});

// Attach API key if set (sync agent uses a dedicated key, not a user JWT)
client.interceptors.request.use((config) => {
  const key = process.env.CLOUD_API_KEY;
  if (key) config.headers["X-API-Key"] = key;
  return config;
});

// Retry on network errors and 5xx responses
axiosRetry(client, {
  retries:        3,
  retryDelay:     axiosRetry.exponentialDelay,
  retryCondition: (err) =>
    axiosRetry.isNetworkOrIdempotentRequestError(err) ||
    (err.response?.status >= 500),
  onRetry: (count, err, config) => {
    logger.warn("Retrying cloud API request", {
      attempt: count,
      url:     config.url,
      error:   err.message,
    });
  },
});

client.interceptors.response.use(
  (res) => res,
  (err) => {
    logger.error("Cloud API error", {
      url:    err.config?.url,
      status: err.response?.status,
      error:  err.message,
    });
    return Promise.reject(err);
  }
);

module.exports = { client };
