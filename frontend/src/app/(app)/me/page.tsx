import { redirect } from 'next/navigation';

export default function MeIndexPage() {
  redirect('/me/resources');
}
