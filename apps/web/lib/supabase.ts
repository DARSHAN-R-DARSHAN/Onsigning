import { createClient, SupabaseClient } from "@supabase/supabase-js";

/**
 * Onsigning Browser Supabase Client
 *
 * Provides a singleton browser client strictly configured via public environment variables.
 * Never references SUPABASE_JWT_SECRET or service-role credentials.
 */

const supabaseUrl = process.env.NEXT_PUBLIC_SUPABASE_URL;
const supabaseAnonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

let clientInstance: SupabaseClient | null = null;

/**
 * Checks whether Supabase public environment variables are configured and well-formed.
 * Validates that NEXT_PUBLIC_SUPABASE_URL is a parseable HTTP(S) URL.
 */
export function isSupabaseConfigured(): boolean {
  if (
    !supabaseUrl ||
    supabaseUrl.trim() === "" ||
    !supabaseAnonKey ||
    supabaseAnonKey.trim() === ""
  ) {
    return false;
  }

  try {
    const parsed = new URL(supabaseUrl.trim());
    return parsed.protocol === "http:" || parsed.protocol === "https:";
  } catch {
    return false;
  }
}

/**
 * Returns a singleton browser SupabaseClient if environment is configured and valid,
 * or null if public configuration is missing or malformed.
 */
export function getSupabaseClient(): SupabaseClient | null {
  if (!isSupabaseConfigured()) {
    return null;
  }

  if (!clientInstance && supabaseUrl && supabaseAnonKey) {
    try {
      clientInstance = createClient(supabaseUrl.trim(), supabaseAnonKey.trim(), {
        auth: {
          persistSession: true,
          autoRefreshToken: true,
          detectSessionInUrl: true,
        },
      });
    } catch {
      clientInstance = null;
    }
  }

  return clientInstance;
}
