'use client';

import { useState, useRef, useEffect } from 'react';
import {
  Bot,
  Send,
  FileText,
  Route,
  Search,
  Sparkles,
  Save,
  User,
} from 'lucide-react';
import { Card } from '@/components/ui/Card';
import { Button } from '@/components/ui/Button';
import { Badge } from '@/components/ui/Badge';
import { EmptyState } from '@/components/ui/EmptyState';
import toast from 'react-hot-toast';

interface Message {
  role: 'user' | 'assistant';
  content: string;
}

const PRESET_RESPONSES: Record<string, string> = {
  '保研':
    '## 保研通用路径\n\n### 大一\n- 打好数学和英语基础\n- 保持GPA在3.5以上\n- 参加1-2个学生组织\n\n### 大二\n- 开始参加学科竞赛（数学建模、电子设计等）\n- 联系老师参与科研项目\n- 争取发表论文\n\n### 大三\n- 确定目标院校（建议3-5所）\n- 准备夏令营申请材料\n- 提升英语成绩（六级500+或雅思6.5+）\n\n### 大四上\n- 参加夏令营/预推免\n- 准备面试\n- 确认录取结果',
  '竞赛':
    '## 南邮推荐竞赛\n\n### 数学类\n- 全国大学生数学建模竞赛（9月）\n- 全国大学生数学竞赛（10月）\n\n### 电子类\n- 全国大学生电子设计竞赛（8月）\n- 嵌入式系统设计大赛\n\n### 计算机类\n- ACM-ICPC国际大学生程序设计竞赛\n- 中国大学生计算机设计大赛\n\n### 创新创业类\n- "互联网+"大学生创新创业大赛\n- "挑战杯"全国大学生课外学术科技作品竞赛',
  '考研':
    '## 考研规划建议\n\n### 大二下学期\n- 确定目标院校和专业\n- 开始背单词、学数学\n\n### 大三上学期\n- 系统复习数学（高数、线代、概率）\n- 英语真题训练\n- 专业课第一轮\n\n### 大三下学期\n- 政治开始复习\n- 数学/英语真题二刷\n- 专业课第二轮强化\n\n### 大四上学期\n- 冲刺阶段：全真模拟\n- 查漏补缺\n- 调整心态\n\n**推荐参考书**\n- 数学：李永乐复习全书\n- 英语：张剑黄皮书\n- 政治：肖秀荣系列',
};

const MODES = [
  { key: 'summary', label: '资料总结', icon: FileText },
  { key: 'plan', label: '路线规划', icon: Route },
  { key: 'search', label: '智能搜索', icon: Search },
];

const QUICK_QUERIES = ['保研', '竞赛', '考研'];

export default function AIPage() {
  const [messages, setMessages] = useState<Message[]>([
    { role: 'assistant', content: '你好！我是南邮知识库AI助手。我可以帮你：\n- 📝 **资料总结**：总结上传的学习资料\n- 🎯 **路线规划**：规划保研/考研/竞赛路线\n- 🔍 **智能搜索**：帮你找到需要的资料\n\n请选择模式或直接提问！' },
  ]);
  const [input, setInput] = useState('');
  const [mode, setMode] = useState('plan');
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    if (!input.trim()) return;

    const userMsg: Message = { role: 'user', content: input };
    setMessages((prev) => [...prev, userMsg]);
    setInput('');

    // Check for quick queries
    setTimeout(() => {
      let response = '';
      for (const [key, value] of Object.entries(PRESET_RESPONSES)) {
        if (input.includes(key)) {
          response = value;
          break;
        }
      }

      if (!response) {
        if (mode === 'plan') {
          response = '## AI路线规划\n\n根据你的描述，我建议以下路径：\n\n1. **基础阶段**：夯实核心课程基础\n2. **提升阶段**：参与科研/竞赛积累经验\n3. **冲刺阶段**：针对性准备目标\n\n如需更详细的规划，请提供：\n- 当前年级和专业\n- 目标方向（保研/考研/就业）\n- 目前的GPA和竞赛经历';
        } else if (mode === 'search') {
          response = '## 智能搜索结果\n\n正在搜索相关资源...\n\n请提供更具体的搜索关键词，例如：\n- "大一 高等数学 期末试卷"\n- "通信工程 保研经验"\n- "数据结构 复习笔记"';
        } else {
          response = '## 资料总结\n\n请上传或指定需要总结的资料。我可以帮你：\n- 提取关键知识点\n- 生成思维导图结构\n- 标注重点和难点\n\n目前支持：PDF、PPT、Word文档';
        }
      }

      const aiMsg: Message = { role: 'assistant', content: response };
      setMessages((prev) => [...prev, aiMsg]);
    }, 800);
  };

  const handleQuickQuery = (query: string) => {
    setInput(query);
  };

  const handleSavePlan = () => {
    toast.success('规划已保存到个人中心');
  };

  const renderMessageContent = (content: string) => {
    // Simple markdown-like rendering
    const lines = content.split('\n');
    return lines.map((line, i) => {
      if (line.startsWith('## ')) {
        return <h3 key={i} className="text-base font-semibold text-gray-900 mt-3 mb-2">{line.slice(3)}</h3>;
      }
      if (line.startsWith('### ')) {
        return <h4 key={i} className="text-sm font-semibold text-gray-800 mt-2 mb-1">{line.slice(4)}</h4>;
      }
      if (line.startsWith('- ')) {
        return <li key={i} className="text-sm text-gray-600 ml-4 list-disc">{line.slice(2)}</li>;
      }
      if (line.startsWith('**') && line.endsWith('**')) {
        return <p key={i} className="text-sm font-bold text-gray-800 mt-2">{line.slice(2, -2)}</p>;
      }
      if (line.trim() === '') return <br key={i} />;
      return <p key={i} className="text-sm text-gray-600">{line}</p>;
    });
  };

  return (
    <div className="max-w-3xl mx-auto space-y-4 h-[calc(100vh-12rem)] flex flex-col">
      {/* Mode Selector */}
      <div className="flex gap-1 bg-gray-100 rounded-xl p-1 w-fit">
        {MODES.map((m) => (
          <button
            key={m.key}
            onClick={() => setMode(m.key)}
            className={`flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium rounded-lg transition-colors ${
              mode === m.key
                ? 'bg-white text-indigo-600 shadow-sm'
                : 'text-gray-500 hover:text-gray-700'
            }`}
          >
            <m.icon className="w-3.5 h-3.5" />
            {m.label}
          </button>
        ))}
      </div>

      {/* Chat area */}
      <Card className="flex-1 flex flex-col overflow-hidden" padding="none">
        {/* Messages */}
        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {messages.map((msg, i) => (
            <div
              key={i}
              className={`flex gap-3 ${msg.role === 'user' ? 'justify-end' : ''}`}
            >
              {msg.role === 'assistant' && (
                <div className="w-8 h-8 rounded-lg bg-indigo-100 flex items-center justify-center flex-shrink-0">
                  <Bot className="w-4 h-4 text-indigo-600" />
                </div>
              )}
              <div
                className={`max-w-[80%] rounded-2xl px-4 py-3 ${
                  msg.role === 'user'
                    ? 'bg-indigo-600 text-white'
                    : 'bg-gray-100 text-gray-900'
                }`}
              >
                {msg.role === 'user' ? (
                  <p className="text-sm">{msg.content}</p>
                ) : (
                  <div>{renderMessageContent(msg.content)}</div>
                )}
              </div>
              {msg.role === 'user' && (
                <div className="w-8 h-8 rounded-full bg-indigo-600 flex items-center justify-center flex-shrink-0">
                  <User className="w-4 h-4 text-white" />
                </div>
              )}
            </div>
          ))}
          <div ref={messagesEndRef} />
        </div>

        {/* Quick queries */}
        <div className="px-4 pb-3 flex items-center gap-2">
          <span className="text-xs text-gray-400">快捷提问：</span>
          {QUICK_QUERIES.map((q) => (
            <button
              key={q}
              onClick={() => handleQuickQuery(q)}
              className="px-2.5 py-1 text-xs bg-indigo-50 text-indigo-600 rounded-full hover:bg-indigo-100 transition-colors"
            >
              {q}
            </button>
          ))}
        </div>

        {/* Input area */}
        <div className="border-t border-gray-200 p-4">
          <div className="flex items-center gap-3">
            <input
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSend()}
              placeholder={
                mode === 'plan' ? '描述你的情况，如：大一电子想保研清华' :
                mode === 'search' ? '输入你想要的资料关键词' :
                '请上传或描述你需要总结的资料'
              }
              className="flex-1 border border-gray-300 rounded-xl px-4 py-2.5 text-sm focus:ring-2 focus:ring-indigo-500 focus:border-indigo-500 outline-none"
            />
            <Button onClick={handleSend} disabled={!input.trim()}>
              <Send className="w-4 h-4" />
            </Button>
          </div>
        </div>
      </Card>

      {/* Save plan button */}
      {messages.length > 2 && (
        <div className="flex justify-center">
          <Button variant="secondary" size="sm" onClick={handleSavePlan}>
            <Save className="w-4 h-4 mr-1.5" /> 保存此规划
          </Button>
        </div>
      )}
    </div>
  );
}
