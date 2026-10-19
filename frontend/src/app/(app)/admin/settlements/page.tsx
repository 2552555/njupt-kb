import { ManagementGuard } from '@/components/workspace/Workspace';
import { SettlementStatements } from '@/components/workspace/SettlementStatements';
export default function AdminSettlementsPage() {
  return <ManagementGuard permission="settlement.manage"><SettlementStatements admin /></ManagementGuard>;
}
