import React, { useMemo } from "react";
import { marked } from "marked";
import hljs from "highlight.js";
import "highlight.js/styles/github-dark.css";

interface MarkdownRendererProps {
  content: string;
  className?: string;
}

export const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({ content, className = "" }) => {
  const html = useMemo(() => {
    if (!content) return "";

    // Strip YAML frontmatter if present for clean document viewing
    let markdownToRender = content;
    const fmMatch = content.match(/^\s*---\s*\n([\s\S]*?)\n---\s*(?:\n|\Z)/);
    let frontmatterBadges = "";

    if (fmMatch) {
      const rawYaml = fmMatch[1];
      markdownToRender = content.slice(fmMatch[0].length);

      const titleMatch = rawYaml.match(/^title:\s*["']?([^"'\n]+)["']?/m);
      const versionMatch = rawYaml.match(/^version:\s*["']?([^"'\n]+)["']?/m);
      const tagsMatch = rawYaml.match(/^tags:\s*\[(.*?)\]/m);

      const badges: string[] = [];
      if (titleMatch) {
        badges.push(`<span class="px-2 py-0.5 rounded bg-sky-500/20 text-sky-300 font-mono text-[10px] border border-sky-500/30 font-semibold">${titleMatch[1]}</span>`);
      }
      if (versionMatch) {
        badges.push(`<span class="px-1.5 py-0.5 rounded bg-purple-500/20 text-purple-300 font-mono text-[10px] border border-purple-500/30">v${versionMatch[1]}</span>`);
      }
      if (tagsMatch) {
        const tags = tagsMatch[1].split(",").map(t => t.trim().replace(/["']/g, "")).filter(Boolean);
        for (const t of tags) {
          badges.push(`<span class="px-1.5 py-0.5 rounded bg-slate-800 text-slate-300 font-mono text-[10px] border border-slate-700/60">${t}</span>`);
        }
      }

      if (badges.length > 0) {
        frontmatterBadges = `<div class="flex flex-wrap gap-1.5 mb-3 pb-2.5 border-b border-slate-800/80">${badges.join("")}</div>`;
      }
    }

    const renderer = new marked.Renderer();

    renderer.code = function ({ text, lang }) {
      const validLang = lang && hljs.getLanguage(lang) ? lang : undefined;
      let highlighted = "";
      try {
        highlighted = validLang
          ? hljs.highlight(text, { language: validLang }).value
          : hljs.highlightAuto(text).value;
      } catch {
        highlighted = text;
      }
      const langBadge = validLang
        ? `<div class="px-2 py-0.5 text-[9px] font-mono text-slate-500 bg-slate-900/90 border-b border-slate-800 uppercase text-right select-none">${validLang}</div>`
        : "";
      return `<div class="my-2.5 rounded-md overflow-hidden border border-slate-800 bg-[#090d16]">${langBadge}<pre class="p-3 text-[11px] overflow-x-auto m-0"><code class="hljs ${validLang || ""}">${highlighted}</code></pre></div>`;
    };

    renderer.codespan = function ({ text }) {
      return `<code class="px-1.5 py-0.5 rounded bg-slate-800/80 text-sky-300 font-mono text-[11px] border border-slate-700/40">${text}</code>`;
    };

    renderer.blockquote = function ({ text }) {
      return `<blockquote class="border-l-2 border-sky-500/60 pl-3 my-2 text-slate-400 italic text-xs bg-sky-950/10 py-1 rounded-r">${text}</blockquote>`;
    };

    renderer.heading = function ({ text, depth }) {
      if (depth === 1) {
        return `<h1 class="text-base font-bold text-slate-100 mt-4 mb-2 pb-1 border-b border-slate-800">${text}</h1>`;
      }
      if (depth === 2) {
        return `<h2 class="text-sm font-semibold text-slate-200 mt-3 mb-1.5 pb-0.5 border-b border-slate-800/50">${text}</h2>`;
      }
      if (depth === 3) {
        return `<h3 class="text-xs font-semibold text-sky-300 mt-2 mb-1">${text}</h3>`;
      }
      return `<h4 class="text-xs font-medium text-slate-300 mt-1.5 mb-1">${text}</h4>`;
    };

    renderer.list = function ({ items, ordered }) {
      const tag = ordered ? "ol" : "ul";
      const cls = ordered ? "list-decimal" : "list-disc";
      const body = items.map(item => `<li class="my-0.5">${item.text}</li>`).join("");
      return `<${tag} class="${cls} list-inside space-y-0.5 my-1.5 pl-1 leading-relaxed text-slate-300">${body}</${tag}>`;
    };

    renderer.paragraph = function ({ text }) {
      return `<p class="mb-2 leading-relaxed text-slate-300 last:mb-0">${text}</p>`;
    };

    renderer.strong = function ({ text }) {
      return `<strong class="font-semibold text-slate-100">${text}</strong>`;
    };

    renderer.em = function ({ text }) {
      return `<em class="italic text-slate-300">${text}</em>`;
    };

    renderer.link = function ({ href, title, text }) {
      const t = title ? ` title="${title}"` : "";
      return `<a href="${href}"${t} target="_blank" rel="noopener noreferrer" class="text-sky-400 hover:text-sky-300 underline underline-offset-2">${text}</a>`;
    };

    renderer.table = function ({ header, rows }) {
      const headerRow = header.map(h => `<th class="border border-slate-800 p-1.5 text-left bg-slate-900 font-semibold text-slate-200 text-[11px]">${h.text}</th>`).join("");
      const bodyRows = rows.map(r => {
        const cells = r.map(c => `<td class="border border-slate-800/80 p-1.5 text-[11px] text-slate-300">${c.text}</td>`).join("");
        return `<tr class="hover:bg-slate-900/40">${cells}</tr>`;
      }).join("");
      return `<div class="overflow-x-auto my-2.5"><table class="w-full border-collapse border border-slate-800 text-[11px]"><thead><tr>${headerRow}</tr></thead><tbody>${bodyRows}</tbody></table></div>`;
    };

    const parsedBody = marked.parse(markdownToRender, { renderer, gfm: true, breaks: true }) as string;
    return frontmatterBadges + parsedBody;
  }, [content]);

  return (
    <div
      className={`prose-markdown select-text leading-relaxed text-xs ${className}`}
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
};
