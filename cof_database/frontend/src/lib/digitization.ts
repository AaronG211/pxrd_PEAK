import { supabase } from "./supabase";

export type DigitizationJobStatus =
  | "draft"
  | "saved"
  | "awaiting_quote"
  | "awaiting_approval"
  | "queued"
  | "running"
  | "review"
  | "completed"
  | "failed"
  | "cancelled";

export type DigitizationJob = {
  id: string;
  status: DigitizationJobStatus;
  sourceFileCount: number;
  sourceTotalBytes: number;
  pageCount: number;
  figureCount: number;
  budgetCapUsd: number;
  clientEstimateUsd: number;
  clientEstimateUpperUsd: number;
  actualCostUsd: number;
  createdAt: string;
  submittedAt: string | null;
  errorSummary: string | null;
};

type DbDigitizationJob = {
  id: string;
  status: DigitizationJobStatus;
  source_file_count: number;
  source_total_bytes: number;
  client_page_count: number;
  client_figure_count: number;
  budget_cap_usd: number | string;
  client_estimate_usd: number | string;
  client_estimate_upper_usd: number | string;
  actual_cost_usd: number | string;
  created_at: string;
  submitted_at: string | null;
  error_summary: string | null;
};

type CreateDigitizationJobInput = {
  files: File[];
  pageCount: number;
  figureCount: number;
  budgetCapUsd: number;
  clientEstimateUsd: number;
  clientEstimateUpperUsd: number;
  onProgress?: (completed: number, total: number) => void;
};

const JOB_FIELDS = [
  "id",
  "status",
  "source_file_count",
  "source_total_bytes",
  "client_page_count",
  "client_figure_count",
  "budget_cap_usd",
  "client_estimate_usd",
  "client_estimate_upper_usd",
  "actual_cost_usd",
  "created_at",
  "submitted_at",
  "error_summary",
].join(", ");

function mapJob(row: DbDigitizationJob): DigitizationJob {
  return {
    id: row.id,
    status: row.status,
    sourceFileCount: row.source_file_count,
    sourceTotalBytes: row.source_total_bytes,
    pageCount: row.client_page_count,
    figureCount: row.client_figure_count,
    budgetCapUsd: Number(row.budget_cap_usd),
    clientEstimateUsd: Number(row.client_estimate_usd),
    clientEstimateUpperUsd: Number(row.client_estimate_upper_usd),
    actualCostUsd: Number(row.actual_cost_usd),
    createdAt: row.created_at,
    submittedAt: row.submitted_at,
    errorSummary: row.error_summary,
  };
}

function requireClient() {
  if (!supabase) throw new Error("Supabase is not configured for this deployment.");
  return supabase;
}

async function fetchJobById(jobId: string): Promise<DigitizationJob | null> {
  const client = requireClient();
  const { data, error } = await client
    .from("digitization_jobs")
    .select(JOB_FIELDS)
    .eq("id", jobId)
    .maybeSingle();
  if (error) throw error;
  return data ? mapJob(data as unknown as DbDigitizationJob) : null;
}

async function currentUserId(): Promise<string> {
  const client = requireClient();
  const {
    data: { user },
    error,
  } = await client.auth.getUser();
  if (error || !user) throw new Error(error?.message ?? "Sign in before managing private jobs.");
  return user.id;
}

async function removeJobObjects(userId: string, jobId: string): Promise<void> {
  const client = requireClient();
  const prefix = `${userId}/${jobId}`;
  const { data, error: listError } = await client.storage
    .from("pxrd-user-uploads")
    .list(prefix, { limit: 100 });
  if (listError) throw listError;

  const paths = (data ?? [])
    .filter((object) => object.name)
    .map((object) => `${prefix}/${object.name}`);
  if (paths.length > 0) {
    const { error: removeError } = await client.storage
      .from("pxrd-user-uploads")
      .remove(paths);
    if (removeError) throw removeError;
  }
}

async function discardJobRecord(jobId: string): Promise<void> {
  const client = requireClient();
  const { error } = await client.rpc("delete_digitization_job_record", {
    p_job_id: jobId,
  });
  if (error) throw error;
}

async function discardDraftJob(userId: string, jobId: string): Promise<void> {
  await removeJobObjects(userId, jobId);
  await discardJobRecord(jobId);
}

async function assertPdfSignatures(files: File[]): Promise<void> {
  for (const file of files) {
    const header = new Uint8Array(await file.slice(0, 5).arrayBuffer());
    const isPdf = header.length === 5
      && header[0] === 0x25
      && header[1] === 0x50
      && header[2] === 0x44
      && header[3] === 0x46
      && header[4] === 0x2d;
    if (!isPdf) throw new Error(`${file.name} does not have a valid PDF file signature.`);
  }
}

export async function fetchDigitizationJobs(): Promise<DigitizationJob[]> {
  const client = requireClient();
  const { data, error } = await client
    .from("digitization_jobs")
    .select(JOB_FIELDS)
    .order("created_at", { ascending: false })
    .limit(20);
  if (error) throw error;
  return ((data ?? []) as unknown as DbDigitizationJob[]).map(mapJob);
}

export async function createDigitizationJob(
  input: CreateDigitizationJobInput,
): Promise<DigitizationJob> {
  const client = requireClient();
  const userId = await currentUserId();
  await assertPdfSignatures(input.files);

  const totalBytes = input.files.reduce((total, file) => total + file.size, 0);
  const { data: created, error: createError } = await client.rpc(
    "create_digitization_job",
    {
      p_source_file_count: input.files.length,
      p_source_total_bytes: totalBytes,
      p_client_page_count: input.pageCount,
      p_client_figure_count: input.figureCount,
      p_budget_cap_usd: input.budgetCapUsd,
      p_client_estimate_usd: input.clientEstimateUsd,
      p_client_estimate_upper_usd: input.clientEstimateUpperUsd,
    },
  );
  if (createError) throw createError;

  const createdRow = (Array.isArray(created) ? created[0] : created) as
    | DbDigitizationJob
    | null;
  if (!createdRow) throw new Error("The draft job could not be created.");
  const jobId = createdRow.id;
  let saved = false;

  try {
    for (const [index, file] of input.files.entries()) {
      const storagePath = `${userId}/${jobId}/${crypto.randomUUID()}.pdf`;
      const { error: uploadError } = await client.storage
        .from("pxrd-user-uploads")
        .upload(storagePath, file, {
          cacheControl: "3600",
          contentType: "application/pdf",
          upsert: false,
      });
      if (uploadError) throw uploadError;

      const { error: manifestError } = await client.rpc("register_digitization_input", {
        p_job_id: jobId,
        p_storage_path: storagePath,
        p_original_name: file.name,
        p_size_bytes: file.size,
      });
      if (manifestError) throw manifestError;
      input.onProgress?.(index + 1, input.files.length);
    }

    const { data: savedRow, error: saveError } = await client.rpc(
      "save_digitization_job",
      { p_job_id: jobId },
    );
    if (saveError) {
      const recovered = await fetchJobById(jobId);
      if (recovered && recovered.status !== "draft") {
        saved = true;
        return recovered;
      }
      throw saveError;
    }
    saved = true;

    const row = (Array.isArray(savedRow) ? savedRow[0] : savedRow) as
      | DbDigitizationJob
      | null;
    if (!row) throw new Error("The saved job could not be read back.");
    return mapJob(row);
  } catch (error) {
    if (!saved) {
      try {
        await discardDraftJob(userId, jobId);
      } catch (cleanupError) {
        const original = error instanceof Error ? error.message : String(error);
        const cleanup = cleanupError instanceof Error ? cleanupError.message : String(cleanupError);
        throw new Error(`${original} Cleanup is incomplete for job ${jobId.slice(0, 8)}: ${cleanup}`);
      }
    }
    throw error;
  }
}

export async function deleteDigitizationJob(jobId: string): Promise<void> {
  const userId = await currentUserId();
  await removeJobObjects(userId, jobId);
  await discardJobRecord(jobId);
}

export function describeWorkspaceError(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  const lower = message.toLowerCase();
  if (
    lower.includes("digitization_jobs")
    || lower.includes("digitization_job_files")
    || lower.includes("pxrd-user-uploads")
    || lower.includes("schema cache")
  ) {
    return "The private workspace has not been initialized in Supabase yet. Run supabase/digitization_workspace.sql, then refresh this page.";
  }
  return message;
}
