'use client';

import { useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import toast from 'react-hot-toast';
import { Avatar } from '@/components/common/Avatar';
import { Button } from '@/components/ui/Button';
import { updateMe, uploadAvatar, type MeResponse } from '@/lib/api/auth';
import { listColleges, listMajors } from '@/lib/api/catalog';
import { describeError } from '@/lib/api/client';
import { useAuthStore } from '@/stores/authStore';

// 同时给扩展名与 MIME：Windows 的文件选择器常把 JPEG 报成 image/jpg / image/pjpeg，
// 只写 image/jpeg 会在系统选择器里直接看不到 .jpg 文件。
const AVATAR_ACCEPT = '.png,.jpg,.jpeg,.webp,.gif,image/png,image/jpeg,image/jpg,image/pjpeg,image/webp,image/gif';
const AVATAR_TYPES = ['image/png', 'image/jpeg', 'image/jpg', 'image/pjpeg', 'image/webp', 'image/gif'];
const AVATAR_MAX_BYTES = 2 * 1024 * 1024;

export default function ProfilePage() {
  const user = useAuthStore(s => s.user);
  return user ? <ProfileForm key={user.id} user={user} /> : null;
}

function ProfileForm({ user }: { user: MeResponse }) {
  const setUser = useAuthStore(s => s.setUser);
  const [name, setName] = useState(user.display_name);
  const [gender, setGender] = useState<NonNullable<MeResponse['gender']>>(user.gender || 'undisclosed');
  const [bio, setBio] = useState(user.bio || '');
  const [college, setCollege] = useState(user.college_id || '');
  const [major, setMajor] = useState(user.major_id || '');
  const [year, setYear] = useState(user.enrollment_year?.toString() || '');
  const [busy, setBusy] = useState(false);
  const [avatarBusy, setAvatarBusy] = useState(false);
  const [avatarName, setAvatarName] = useState<string | null>(null);
  const avatarInput = useRef<HTMLInputElement>(null);
  const inputClass = 'mt-1 min-h-11 w-full border border-control-border bg-surface px-3 py-2 text-sm';
  const colleges = useQuery({ queryKey: ['profile-colleges'], queryFn: listColleges });
  const majors = useQuery({ queryKey: ['profile-majors', college], queryFn: () => listMajors(college || undefined), enabled: !!college });

  async function pickAvatar(file: File) {
    // 归一浏览器给出的非标准 MIME，避免 .jpg 被误拒。
    const type = file.type === 'image/jpg' || file.type === 'image/pjpeg' ? 'image/jpeg' : file.type;
    if (!AVATAR_TYPES.includes(file.type) && !AVATAR_TYPES.includes(type)) {
      toast.error('头像仅支持 PNG、JPG、WebP 或 GIF');
      return;
    }
    if (file.size <= 0 || file.size > AVATAR_MAX_BYTES) { toast.error('头像需在 0–2 MiB 内'); return; }
    setAvatarBusy(true);
    try {
      setUser(await uploadAvatar(file));
      toast.success('头像已更新');
    } catch (error) { toast.error(describeError(error).message); } finally { setAvatarBusy(false); }
  }

  async function clearAvatar() {
    setAvatarBusy(true);
    try {
      setUser(await updateMe({ avatar_url: null }));
      toast.success('已恢复默认头像');
    } catch (error) { toast.error(describeError(error).message); } finally { setAvatarBusy(false); }
  }

  return <form className="max-w-2xl space-y-5 border border-divider bg-surface p-5" onSubmit={async e => {
    e.preventDefault(); setBusy(true);
    try {
      setUser(await updateMe({
        display_name: name.trim(), gender, bio: bio.trim() || null,
        college_id: college || null, major_id: major || null,
        enrollment_year: year ? Number(year) : null,
      }));
      toast.success('个人资料已保存');
    } catch (error) { toast.error(describeError(error).message); } finally { setBusy(false); }
  }}>
    <h2 className="text-xl font-semibold">个人资料</h2>
    <div className="flex flex-wrap items-center gap-3 border-b border-divider pb-4">
      <Avatar url={user.avatar_url} name={name} />
      <div className="flex flex-wrap items-center gap-2">
        <input
          ref={avatarInput}
          aria-label="上传头像"
          type="file"
          accept={AVATAR_ACCEPT}
          disabled={avatarBusy}
          onChange={e => {
            const file = e.target.files?.[0];
            e.target.value = '';
            if (file) { setAvatarName(file.name); void pickAvatar(file); }
          }}
          className="sr-only"
        />
        <Button
          type="button"
          variant="secondary"
          disabled={avatarBusy}
          onClick={() => avatarInput.current?.click()}
        >
          {avatarBusy ? '上传中…' : '更换头像'}
        </Button>
        {avatarName && (
          <span className="max-w-40 truncate text-xs text-text-secondary" title={avatarName}>
            {avatarName}
          </span>
        )}
        {user.avatar_url && (
          <Button type="button" variant="ghost" disabled={avatarBusy} onClick={() => { setAvatarName(null); void clearAvatar(); }}>
            恢复默认头像
          </Button>
        )}
      </div>
      <p className="w-full text-xs text-text-secondary">支持 PNG、JPG、WebP、GIF，不超过 2 MiB。</p>
    </div>
    <fieldset disabled={busy} className="space-y-4">
      <label className="block text-sm">昵称<input required maxLength={80} value={name} onChange={e => setName(e.target.value)} className={inputClass} /></label>
      <label className="block text-sm">性别<select value={gender} onChange={e => setGender(e.target.value as typeof gender)} className={inputClass}><option value="undisclosed">不透露</option><option value="female">女</option><option value="male">男</option><option value="nonbinary">非二元</option></select></label>
      <div className="grid gap-4 sm:grid-cols-2">
        <label className="block text-sm">学院
          <select value={college} onChange={e => { setCollege(e.target.value); setMajor(''); }} className={inputClass}>
            <option value="">未填写</option>
            {colleges.data?.map(c => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
          {colleges.error && <span role="alert" className="mt-1 block text-xs text-crimson">{describeError(colleges.error).message}</span>}
        </label>
        <label className="block text-sm">专业
          <select value={major} onChange={e => setMajor(e.target.value)} disabled={!college} className={inputClass}>
            <option value="">未填写</option>
            {majors.data?.map(m => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
          {majors.error && <span role="alert" className="mt-1 block text-xs text-crimson">{describeError(majors.error).message}</span>}
        </label>
      </div>
      <label className="block text-sm">简介<textarea maxLength={500} rows={3} value={bio} onChange={e => setBio(e.target.value)} className={inputClass} /></label>
      <label className="block text-sm">入学年份<input type="number" min={1900} max={2100} value={year} onChange={e => setYear(e.target.value)} className={inputClass} /></label>
      <Button type="submit" isLoading={busy} disabled={!name.trim()}>保存资料</Button>
    </fieldset>
  </form>;
}
