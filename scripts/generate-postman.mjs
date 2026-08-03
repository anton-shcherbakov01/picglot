#!/usr/bin/env node
/**
 * Generate a Postman collection from the OpenAPI document.
 *
 * The collection is generated rather than hand-maintained so it cannot drift
 * from the API. Regenerate after changing routes:
 *
 *   python -m picglot.cli openapi --output docs/api/openapi.json
 *   node scripts/generate-postman.mjs
 */
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const specPath = resolve(root, "docs/api/openapi.json");
const outPath = resolve(root, "docs/api/picglot.postman_collection.json");

let spec;
try {
  spec = JSON.parse(readFileSync(specPath, "utf8"));
} catch (error) {
  console.error(
    `Could not read ${specPath}.\n` +
      "Generate it first:  python -m picglot.cli openapi --output docs/api/openapi.json",
  );
  process.exit(1);
}

const METHODS = ["get", "post", "put", "patch", "delete", "head", "options"];

/** A minimal example value for a schema, enough to make the request runnable. */
function exampleFor(schema, spec, depth = 0) {
  if (!schema || depth > 6) return null;
  if (schema.$ref) {
    const name = schema.$ref.split("/").pop();
    return exampleFor(spec.components?.schemas?.[name], spec, depth + 1);
  }
  if (schema.example !== undefined) return schema.example;
  if (schema.default !== undefined) return schema.default;
  if (schema.enum?.length) return schema.enum[0];
  for (const key of ["anyOf", "oneOf", "allOf"]) {
    if (Array.isArray(schema[key]) && schema[key].length) {
      return exampleFor(schema[key][0], spec, depth + 1);
    }
  }
  switch (schema.type) {
    case "array":
      return [exampleFor(schema.items, spec, depth + 1)].filter(
        (v) => v !== null,
      );
    case "integer":
    case "number":
      return 0;
    case "boolean":
      return false;
    case "string":
      return schema.format === "date-time" ? new Date(0).toISOString() : "";
    case "object":
    default: {
      if (!schema.properties) return {};
      const out = {};
      for (const [name, prop] of Object.entries(schema.properties)) {
        out[name] = exampleFor(prop, spec, depth + 1);
      }
      return out;
    }
  }
}

function buildRequest(path, method, op) {
  const rawUrl = `{{baseUrl}}${path}`;
  const query = [];
  const headers = [];
  const variables = [];

  for (const param of op.parameters ?? []) {
    if (param.in === "query") {
      query.push({
        key: param.name,
        value: String(exampleFor(param.schema, spec) ?? ""),
        description: param.description ?? "",
        disabled: !param.required,
      });
    } else if (param.in === "header") {
      headers.push({ key: param.name, value: "", disabled: !param.required });
    } else if (param.in === "path") {
      variables.push({
        key: param.name,
        value: "",
        description: param.description ?? "",
      });
    }
  }

  let body;
  const content = op.requestBody?.content ?? {};
  if (content["application/json"]) {
    headers.push({ key: "Content-Type", value: "application/json" });
    body = {
      mode: "raw",
      raw: JSON.stringify(
        exampleFor(content["application/json"].schema, spec) ?? {},
        null,
        2,
      ),
      options: { raw: { language: "json" } },
    };
  } else if (content["multipart/form-data"]) {
    const props = content["multipart/form-data"].schema?.properties ?? {};
    body = {
      mode: "formdata",
      formdata: Object.entries(props).map(([key, prop]) => ({
        key,
        // A binary property is a file picker in Postman; everything else is text.
        type: prop?.format === "binary" ? "file" : "text",
        value:
          prop?.format === "binary"
            ? undefined
            : String(exampleFor(prop, spec) ?? ""),
        src: prop?.format === "binary" ? [] : undefined,
      })),
    };
  }

  const segments = path.split("/").filter(Boolean);
  return {
    name: op.summary || `${method.toUpperCase()} ${path}`,
    request: {
      method: method.toUpperCase(),
      header: headers,
      url: {
        raw: query.length
          ? `${rawUrl}?${query.map((q) => `${q.key}=${q.value}`).join("&")}`
          : rawUrl,
        host: ["{{baseUrl}}"],
        path: segments,
        query: query.length ? query : undefined,
        variable: variables.length ? variables : undefined,
      },
      description: op.description ?? "",
      ...(body ? { body } : {}),
    },
    response: [],
  };
}

const folders = new Map();
for (const [path, item] of Object.entries(spec.paths ?? {})) {
  for (const method of METHODS) {
    const op = item[method];
    if (!op) continue;
    const tag = op.tags?.[0] ?? "default";
    if (!folders.has(tag)) folders.set(tag, []);
    folders.get(tag).push(buildRequest(path, method, op));
  }
}

const collection = {
  info: {
    name: `${spec.info?.title ?? "PicGlot"} API`,
    description:
      `${spec.info?.description ?? ""}\n\n` +
      "Generated from docs/api/openapi.json by scripts/generate-postman.mjs — " +
      "do not edit by hand, regenerate instead.",
    version: spec.info?.version ?? "1.0.0",
    schema:
      "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
  },
  auth: {
    type: "bearer",
    bearer: [{ key: "token", value: "{{apiKey}}", type: "string" }],
  },
  variable: [
    { key: "baseUrl", value: "http://localhost:8000", type: "string" },
    { key: "apiKey", value: "", type: "string" },
  ],
  item: [...folders.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([name, item]) => ({ name, item })),
};

writeFileSync(outPath, `${JSON.stringify(collection, null, 2)}\n`, "utf8");
const count = [...folders.values()].reduce((sum, reqs) => sum + reqs.length, 0);
console.log(`wrote ${outPath} (${count} requests in ${folders.size} folders)`);
