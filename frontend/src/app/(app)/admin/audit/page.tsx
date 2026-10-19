import { AuditWorkspace } from '@/components/workspace/AuditWorkspace';
export default async function Page({ searchParams }: { searchParams: Promise<{ object_type?: string; object_id?: string }> }) {
  const params = await searchParams;
  return <AuditWorkspace initialType={params.object_type} initialId={params.object_id} />;
}
