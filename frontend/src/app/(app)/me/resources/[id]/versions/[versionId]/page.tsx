'use client';

import { useParams } from 'next/navigation';
import { VersionWorkspace } from '@/components/resource/VersionWorkspace';

export default function VersionPage() {
  const { id, versionId } = useParams<{ id: string; versionId: string }>();
  return <VersionWorkspace resourceId={id} versionId={versionId} />;
}
