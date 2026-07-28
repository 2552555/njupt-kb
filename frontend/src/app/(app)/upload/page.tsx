'use client';

import { useState, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { useQuery, useMutation } from '@tanstack/react-query';
import { useDropzone } from 'react-dropzone';
import { UploadCloud, File, X, Search } from 'lucide-react';
import api from '@/lib/api';
import { formatFileSize } from '@/lib/utils';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Input } from '@/components/ui/Input';
import { Badge } from '@/components/ui/Badge';
import toast from 'react-hot-toast';

const RESOURCE_TYPES = [
  { value: 'exam', label: '试卷' },
  { value: 'ppt', label: 'PPT课件' },
  { value: 'note', label: '笔记' },
  { value: 'review', label: '复习资料' },
  { value: 'lab_report', label: '实验报告' },
  { value: 'textbook', label: '教材' },
  { value: 'code', label: '代码' },
  { value: 'other', label: '其他' },
];

export default function UploadPage() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [resourceType, setResourceType] = useState('other');
  const [year, setYear] = useState(new Date().getFullYear().toString());
  const [semester, setSemester] = useState('1');
  const [courseSearch, setCourseSearch] = useState('');
  const [selectedCourse, setSelectedCourse] = useState<number | null>(null);
  const [selectedCourseName, setSelectedCourseName] = useState('');
  const [pageCount, setPageCount] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  const { data: coursesData } = useQuery({
    queryKey: ['courses-search', courseSearch],
    queryFn: () => {
      if (courseSearch.length < 2) return [];
      return api.get(`/courses?search=${encodeURIComponent(courseSearch)}`).then((r) => r.data);
    },
    enabled: courseSearch.length >= 2,
  });

  const courses = Array.isArray(coursesData) ? coursesData : [];

  const onDrop = useCallback((acceptedFiles: File[]) => {
    if (acceptedFiles.length > 0) {
      setFile(acceptedFiles[0]);
      if (!title) {
        // Auto-fill title from filename
        const name = acceptedFiles[0].name.replace(/\.[^/.]+$/, '');
        setTitle(name);
      }
    }
  }, [title]);

  const { getRootProps, getInputProps, isDragActive } = useDropzone({
    onDrop,
    maxFiles: 1,
    accept: {
      'application/pdf': ['.pdf'],
      'application/msword': ['.doc'],
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document': ['.docx'],
      'application/vnd.ms-powerpoint': ['.ppt', '.pptx'],
      'application/zip': ['.zip'],
      'application/x-rar-compressed': ['.rar'],
    },
  });

  const handleSubmit = async () => {
    if (!file) {
      toast.error('请选择文件');
      return;
    }
    if (!title.trim()) {
      toast.error('请输入标题');
      return;
    }
    if (!selectedCourse) {
      toast.error('请选择课程');
      return;
    }

    setIsSubmitting(true);
    try {
      const formData = new FormData();
      formData.append('file', file);
      formData.append('title', title);
      formData.append('description', description);
      formData.append('resource_type', resourceType);
      formData.append('course_id', String(selectedCourse));
      formData.append('year', year);
      formData.append('semester', semester);
      if (pageCount) formData.append('pages', pageCount);

      await api.post('/resources', formData, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
      toast.success('上传成功！');
      router.push('/resources');
    } catch (err: unknown) {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail || '上传失败，请重试';
      toast.error(msg);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <h1 className="text-xl font-bold text-gray-900">上传资料</h1>

      {/* Drop zone */}
      <Card>
        <div
          {...getRootProps()}
          className={`border-2 border-dashed rounded-xl p-8 text-center cursor-pointer transition-colors ${
            isDragActive
              ? 'border-indigo-500 bg-indigo-50'
              : 'border-gray-300 hover:border-indigo-400 hover:bg-gray-50'
          }`}
        >
          <input {...getInputProps()} />
          {file ? (
            <div className="flex items-center justify-center gap-3">
              <File className="w-8 h-8 text-indigo-600" />
              <div className="text-left">
                <p className="text-sm font-medium text-gray-900">{file.name}</p>
                <p className="text-xs text-gray-500">{formatFileSize(file.size)}</p>
              </div>
              <button
                onClick={(e) => {
                  e.stopPropagation();
                  setFile(null);
                }}
                className="p-1 hover:bg-gray-100 rounded"
              >
                <X className="w-4 h-4 text-gray-400" />
              </button>
            </div>
          ) : (
            <>
              <UploadCloud className="w-10 h-10 text-gray-400 mx-auto mb-3" />
              <p className="text-sm text-gray-600">
                拖拽文件到此处，或<span className="text-indigo-600">点击选择</span>
              </p>
              <p className="text-xs text-gray-400 mt-1">
                支持 PDF, Word, PPT, ZIP, RAR
              </p>
            </>
          )}
        </div>
      </Card>

      {/* Form */}
      <Card>
        <div className="space-y-4">
          <Input
            label="标题"
            placeholder="资料标题"
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />

          <Input
            label="描述"
            placeholder="简要描述资料内容"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />

          {/* Resource type */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">资料类型</label>
            <div className="flex flex-wrap gap-2">
              {RESOURCE_TYPES.map((t) => (
                <button
                  key={t.value}
                  onClick={() => setResourceType(t.value)}
                  className={`px-3 py-1.5 text-xs font-medium rounded-full transition-colors ${
                    resourceType === t.value
                      ? 'bg-indigo-600 text-white'
                      : 'bg-gray-100 text-gray-600 hover:bg-gray-200'
                  }`}
                >
                  {t.label}
                </button>
              ))}
            </div>
          </div>

          {/* Course selector */}
          <div>
            <label className="block text-sm font-medium text-gray-700 mb-2">课程</label>
            {selectedCourse ? (
              <div className="flex items-center gap-2 p-2 bg-indigo-50 rounded-lg">
                <span className="text-sm text-indigo-700">{selectedCourseName}</span>
                <button
                  onClick={() => { setSelectedCourse(null); setSelectedCourseName(''); }}
                  className="text-indigo-400 hover:text-indigo-600"
                >
                  <X className="w-4 h-4" />
                </button>
              </div>
            ) : (
              <div>
                <div className="relative">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-gray-400" />
                  <input
                    type="text"
                    placeholder="搜索课程..."
                    value={courseSearch}
                    onChange={(e) => setCourseSearch(e.target.value)}
                    className="w-full pl-10 pr-4 py-2 text-sm border border-gray-300 rounded-lg focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
                  />
                </div>
                {courses.length > 0 && (
                  <div className="mt-1 border border-gray-200 rounded-lg max-h-40 overflow-y-auto">
                    {courses.slice(0, 10).map((c: Record<string, unknown>) => (
                      <button
                        key={c.id as number}
                        onClick={() => {
                          setSelectedCourse(c.id as number);
                          setSelectedCourseName(c.name as string);
                          setCourseSearch('');
                        }}
                        className="w-full text-left px-3 py-2 text-sm hover:bg-indigo-50 transition-colors"
                      >
                        {c.name as string}
                        <span className="text-xs text-gray-400 ml-2">{(c as Record<string, unknown>).code as string}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Year & Semester */}
          <div className="grid grid-cols-2 gap-4">
            <Input
              label="年份"
              type="number"
              value={year}
              onChange={(e) => setYear(e.target.value)}
              placeholder="2026"
            />
            <div>
              <label className="block text-sm font-medium text-gray-700 mb-1">学期</label>
              <select
                value={semester}
                onChange={(e) => setSemester(e.target.value)}
                className="w-full border border-gray-300 rounded-lg px-3 py-2 text-sm"
              >
                <option value="1">第一学期</option>
                <option value="2">第二学期</option>
                <option value="3">第三学期（暑期）</option>
              </select>
            </div>
          </div>

          <Input
            label="页数（可选）"
            type="number"
            value={pageCount}
            onChange={(e) => setPageCount(e.target.value)}
            placeholder="如 100"
          />

          <Button
            className="w-full"
            size="lg"
            onClick={handleSubmit}
            isLoading={isSubmitting}
          >
            上传资料
          </Button>
        </div>
      </Card>
    </div>
  );
}
