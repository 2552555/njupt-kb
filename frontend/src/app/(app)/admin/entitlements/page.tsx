import { EntitlementWorkspace } from '@/components/workspace/EntitlementWorkspace';
export default async function Page({ searchParams }: { searchParams: Promise<{ user_id?: string }> }) {
  const params = await searchParams;
  return <EntitlementWorkspace initialUser={params.user_id} />;
}
