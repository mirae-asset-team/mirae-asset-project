import { randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { neon } from "@neondatabase/serverless";
import { extractReceipts } from "./receipts";
import {
  ACCURACY_LABELS,
  FACT_LABELS,
  type AccuracyVerdict,
  type FactVerdict,
  type LedgerRecord,
} from "./types";

const LOCAL_FILE = path.join(process.cwd(), "data", "ledger.json");

function databaseUrl(): string {
  return process.env.DATABASE_URL || process.env.POSTGRES_URL || "";
}

function nowIso(): string {
  return new Date().toISOString();
}

function normalize(input: {
  author: string;
  transcript: string;
  fact: string;
  accuracy: string;
  improvement?: string;
  notes?: string;
}): Omit<LedgerRecord, "id" | "created_at"> {
  const author = input.author.trim();
  const transcript = input.transcript.trim();
  if (!author) throw new Error("author_required");
  if (!transcript) throw new Error("transcript_required");
  if (!(input.fact in FACT_LABELS)) throw new Error("fact_invalid");
  if (!(input.accuracy in ACCURACY_LABELS)) throw new Error("accuracy_invalid");
  if (author.length > 80) throw new Error("author_too_long");
  if (transcript.length > 20000) throw new Error("transcript_too_long");
  const improvement = (input.improvement || "").trim();
  const notes = (input.notes || "").trim();
  if (improvement.length > 8000) throw new Error("improvement_too_long");
  if (notes.length > 8000) throw new Error("notes_too_long");
  return {
    author,
    transcript,
    fact: input.fact as FactVerdict,
    accuracy: input.accuracy as AccuracyVerdict,
    improvement,
    notes,
    receipts: extractReceipts(`${transcript}\n${notes}\n${improvement}`),
  };
}

async function ensurePostgres() {
  const url = databaseUrl();
  if (!url) throw new Error("database_url_required");
  const sql = neon(url);
  await sql`CREATE TABLE IF NOT EXISTS ledger (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    author TEXT NOT NULL,
    transcript TEXT NOT NULL,
    fact TEXT NOT NULL,
    accuracy TEXT NOT NULL,
    improvement TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    receipts JSONB NOT NULL DEFAULT '[]'::jsonb
  )`;
  return sql;
}

async function readLocal(): Promise<LedgerRecord[]> {
  try {
    const raw = await readFile(LOCAL_FILE, "utf8");
    const parsed = JSON.parse(raw) as LedgerRecord[];
    return Array.isArray(parsed) ? parsed : [];
  } catch {
    return [];
  }
}

async function writeLocal(rows: LedgerRecord[]): Promise<void> {
  await mkdir(path.dirname(LOCAL_FILE), { recursive: true });
  await writeFile(LOCAL_FILE, JSON.stringify(rows, null, 2), "utf8");
}

export async function listRecords(): Promise<LedgerRecord[]> {
  if (databaseUrl()) {
    const sql = await ensurePostgres();
    const rows = (await sql`SELECT id, created_at, author, transcript, fact, accuracy, improvement, notes, receipts
      FROM ledger ORDER BY created_at DESC LIMIT 300`) as Array<Record<string, unknown>>;
    return rows.map((row) => ({
      id: String(row.id),
      created_at: String(row.created_at),
      author: String(row.author),
      transcript: String(row.transcript),
      fact: row.fact as FactVerdict,
      accuracy: row.accuracy as AccuracyVerdict,
      improvement: String(row.improvement || ""),
      notes: String(row.notes || ""),
      receipts: Array.isArray(row.receipts) ? row.receipts.map(String) : [],
    }));
  }
  if (process.env.VERCEL) {
    throw new Error("database_url_required");
  }
  const rows = await readLocal();
  return rows.sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 300);
}

export async function addRecord(input: {
  author: string;
  transcript: string;
  fact: string;
  accuracy: string;
  improvement?: string;
  notes?: string;
}): Promise<LedgerRecord> {
  const body = normalize(input);
  const record: LedgerRecord = { id: randomUUID(), created_at: nowIso(), ...body };
  if (databaseUrl()) {
    const sql = await ensurePostgres();
    await sql`INSERT INTO ledger (id, created_at, author, transcript, fact, accuracy, improvement, notes, receipts)
      VALUES (${record.id}, ${record.created_at}, ${record.author}, ${record.transcript}, ${record.fact},
      ${record.accuracy}, ${record.improvement}, ${record.notes}, ${JSON.stringify(record.receipts)}::jsonb)`;
    return record;
  }
  if (process.env.VERCEL) {
    throw new Error("database_url_required");
  }
  const rows = await readLocal();
  rows.push(record);
  await writeLocal(rows);
  return record;
}

export function summarize(rows: LedgerRecord[]) {
  const facts: Record<string, number> = { correct: 0, partial: 0, incorrect: 0, unknown: 0 };
  const accuracy: Record<string, number> = { high: 0, medium: 0, low: 0, abstain_ok: 0 };
  for (const row of rows) {
    facts[row.fact] = (facts[row.fact] || 0) + 1;
    accuracy[row.accuracy] = (accuracy[row.accuracy] || 0) + 1;
  }
  return { total: rows.length, facts, accuracy };
}
