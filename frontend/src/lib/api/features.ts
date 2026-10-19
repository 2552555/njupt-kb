import { api } from './client';

export interface FeatureFlags {
  commerce_enabled: boolean;
  ai_enabled: boolean;
}

export async function fetchFeatures(): Promise<FeatureFlags> {
  return (await api.get<FeatureFlags>('/features')).data;
}
