"use server";

import { revalidatePath } from "next/cache";
import { addRecord } from "@/lib/store";

export async function saveRecord(formData: FormData): Promise<{ ok: true } | { ok: false; error: string }> {
  try {
    await addRecord({
      author: String(formData.get("author") || ""),
      transcript: String(formData.get("transcript") || ""),
      fact: String(formData.get("fact") || ""),
      accuracy: String(formData.get("accuracy") || ""),
      improvement: String(formData.get("improvement") || ""),
      notes: String(formData.get("notes") || ""),
    });
    revalidatePath("/");
    return { ok: true };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : "save_failed" };
  }
}
