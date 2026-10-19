import { notFound } from "next/navigation";
import { RequestWorkspace } from "@/components/common/RequestWorkspace";
import { isUuid } from "@/lib/utils";
export const metadata = { title: "资料需求" };
export default async function RequestPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  if (!isUuid(id)) notFound();
  return <RequestWorkspace requestId={id} />;
}
