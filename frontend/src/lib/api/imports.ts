import { api } from './client';
import type { ResourceCreate, ResourceCreatedOut } from './content';
import type { ResourceType } from './types';

export interface Classification {
  name: string;
  course_id: string | null;
  candidates: { id: string; name: string; code: string | null }[];
  resource_type: ResourceType;
  reason: string;
  needs_confirmation: boolean;
  type_matches: ResourceType[];
}

export async function classifyFiles(names: string[]): Promise<Classification[]> {
  return (await api.post<{ items: Classification[] }>('/admin/imports/classify', { names })).data.items;
}

export async function createImportDraft(payload: ResourceCreate, key: string): Promise<ResourceCreatedOut> {
  return (await api.post<ResourceCreatedOut>('/admin/imports/drafts', payload, { headers: { 'Idempotency-Key': key } })).data;
}
