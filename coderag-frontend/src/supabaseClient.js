import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || 'https://mhpnecdueyhxyhzmpcwk.supabase.co';
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1ocG5lY2R1ZXloeHloem1wY3drIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzgzNzY5MjEsImV4cCI6MjA5Mzk1MjkyMX0.NzJg22hssbsaqcmFTzrb9C6aKUtSFfAUfRM7gxfWYJI';

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
