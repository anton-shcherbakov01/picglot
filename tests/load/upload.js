import http from "k6/http";
import { check, sleep } from "k6";

const API = __ENV.API_URL || "http://localhost:8000";

export const options = {
  stages: [
    { duration: "30s", target: 10 },
    { duration: "1m", target: 30 },
    { duration: "30s", target: 0 },
  ],
  thresholds: {
    http_req_duration: ["p(95)<2000"],
    http_req_failed: ["rate<0.05"],
  },
};

export default function () {
  const health = http.get(`${API}/health/live`);
  check(health, { "health 200": (r) => r.status === 200 });

  const config = http.get(`${API}/api/v1/config`);
  check(config, {
    "config 200": (r) => r.status === 200,
    "has tools": (r) => r.json("tools") !== undefined,
  });

  const langs = http.get(`${API}/api/v1/languages`);
  check(langs, { "languages 200": (r) => r.status === 200 });

  sleep(1);
}
