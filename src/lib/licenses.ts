/**
 * License registry lookup helper.
 *
 * Loads `data/model-licenses.json` (keyed by checkpoint filename) and returns
 * the matching record, or a default "unverified" record when the model is
 * missing or the JSON is malformed.
 *
 * Contract:
 *   - Record shape: {source_url, licence, commercial_use, note, checked}
 *   - commercial_use: 'yes' | 'conditional' | 'no' | 'unverified'
 *   - Never throws on malformed JSON; returns all-unverified default.
 */

import * as fs from "fs";
import * as path from "path";

const LICENSES_FILE = path.resolve(
  __dirname,
  "..",
  "..",
  "data",
  "model-licenses.json"
);

interface LicenseRecord {
  source_url: string | null;
  licence: string | null;
  commercial_use: "yes" | "conditional" | "no" | "unverified";
  note: string | null;
  checked: string | null;
}

const DEFAULT_RECORD: LicenseRecord = {
  source_url: null,
  licence: null,
  commercial_use: "unverified",
  note: null,
  checked: null,
};

let cache: Record<string, LicenseRecord> | null = null;

function loadRegistry(): Record<string, LicenseRecord> {
  if (cache !== null) return cache;
  try {
    const raw = fs.readFileSync(LICENSES_FILE, "utf-8");
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      cache = parsed as Record<string, LicenseRecord>;
      return cache;
    }
  } catch {
    // malformed JSON or missing file — fall through to empty registry
  }
  cache = {};
  return cache;
}

/**
 * Look up license info for a checkpoint filename.
 * Returns the record from the registry, or a default unverified record if
 * the model is not present or the registry cannot be loaded.
 */
export function getLicenseInfo(filename: string): LicenseRecord {
  const registry = loadRegistry();
  const record = registry[filename];
  if (!record || typeof record !== "object") {
    return { ...DEFAULT_RECORD };
  }
  // Normalize commercial_use to a known value
  const commercialUse =
    record.commercial_use === "yes" ||
    record.commercial_use === "conditional" ||
    record.commercial_use === "no"
      ? record.commercial_use
      : "unverified";
  return {
    source_url: typeof record.source_url === "string" ? record.source_url : null,
    licence: typeof record.licence === "string" ? record.licence : null,
    commercial_use,
    note: typeof record.note === "string" ? record.note : null,
    checked: typeof record.checked === "string" ? record.checked : null,
  };
}

/** Badge color for a commercial_use value. */
export function commercialUseBadge(
  commercialUse: LicenseRecord["commercial_use"]
): string {
  switch (commercialUse) {
    case "yes":
      return "green";
    case "conditional":
      return "amber";
    case "no":
      return "red";
    case "unverified":
    default:
      return "grey";
  }
}

export type { LicenseRecord };
