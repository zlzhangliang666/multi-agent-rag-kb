/**
 * 轻量 Markdown 渲染器（零第三方依赖，配合 v-html 使用）
 *
 * 支持：标题、粗体、斜体、删除线、行内代码、围栏代码块、有序/无序列表、
 *      引用、分隔线、链接、表格、段落与软换行。
 *
 * 安全策略：
 * 1. 代码块先抽出并用占位符保护；
 * 2. 其余全文先做 HTML 转义（& < > " '），因此模型输出的原始 HTML 不会执行；
 * 3. 转义之后再做 Markdown 语法替换；
 * 4. 链接 href 仅允许 http/https/mailto/相对路径与锚点，杜绝 javascript: 注入。
 */

const CODE_BLOCK_RE = /```([^\n`]*)\n?([\s\S]*?)(?:```|$)/g
const INLINE_CODE_RE = /`([^`\n]+)`/g
const MERMAID_BLOCK_RE = /```[ \t]*mermaid[ \t]*\r?\n([\s\S]*?)```/gi

/**
 * 从 Markdown 文本中抽出 ```mermaid 代码块
 *
 * 用于「让模型自绘图」场景：正文里的 Mermaid 片段交给 mermaid 渲染成 SVG，
 * 从正文中移除，避免和普通代码块一起以纯文本形式展示。
 *
 * @param {string} source 原始文本
 * @returns {{text:string, blocks:string[]}} 去掉代码块的文本 + Mermaid 源码数组
 */
export function extractMermaid(source) {
  const blocks = []
  const text = String(source || '').replace(MERMAID_BLOCK_RE, (_, code) => {
    const body = String(code || '').trim()
    if (body) blocks.push(body)
    return ''
  })
  return { text, blocks }
}

export function escapeHtml(text) {
  return String(text === null || text === undefined ? '' : text)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

function safeUrl(url) {
  const u = String(url || '').trim()
  if (/^(https?:|mailto:|tel:|#|\/)/i.test(u)) return u
  return '#'
}

/**
 * 行内语法处理（入参必须已经过 escapeHtml）
 */
function inlineFormat(escaped) {
  const codes = []
  let out = String(escaped).replace(INLINE_CODE_RE, (_, code) => {
    codes.push(code)
    return `\u0001${codes.length - 1}\u0001`
  })

  out = out
    .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
    .replace(/__([^_]+)__/g, '<strong>$1</strong>')
    .replace(/(^|[^*\w])\*([^*\n]+)\*/g, '$1<em>$2</em>')
    .replace(/~~([^~]+)~~/g, '<del>$1</del>')
    .replace(
      /\[([^\]]*)\]\(([^)\s]+)\)/g,
      (_, text, url) => `<a href="${escapeHtml(safeUrl(url))}" target="_blank" rel="noopener noreferrer">${text || escapeHtml(url)}</a>`,
    )

  return out.replace(/\u0001(\d+)\u0001/g, (_, idx) => `<code class="md-inline-code">${codes[Number(idx)]}</code>`)
}

function splitTableRow(line) {
  return line
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim())
}

function isTableSeparator(line) {
  return /^\|?[\s:|-]+\|?$/.test(line) && line.includes('-')
}

function buildTable(headerCells, bodyRows) {
  const thead = headerCells.map((cell) => `<th>${inlineFormat(cell)}</th>`).join('')
  const tbody = bodyRows
    .map((row) => `<tr>${row.map((cell) => `<td>${inlineFormat(cell)}</td>`).join('')}</tr>`)
    .join('')
  return `<div class="md-table-wrap"><table class="md-table"><thead><tr>${thead}</tr></thead><tbody>${tbody}</tbody></table></div>`
}

/**
 * Markdown -> HTML 字符串
 * @param {string} source 原始 Markdown 文本
 * @returns {string} 可直接用于 v-html 的 HTML
 */
export function renderMarkdown(source) {
  if (source === null || source === undefined || source === '') return ''

  let text = String(source).replace(/\r\n?/g, '\n')

  // 1) 抽出围栏代码块
  const codeBlocks = []
  text = text.replace(CODE_BLOCK_RE, (_, lang, code) => {
    const language = String(lang || '').trim()
    const body = String(code || '').replace(/\n$/, '')
    codeBlocks.push(
      `<pre class="md-pre"${language ? ` data-lang="${escapeHtml(language)}"` : ''}><code>${escapeHtml(body)}</code></pre>`,
    )
    return `\n\u0000MDBLOCK${codeBlocks.length - 1}\u0000\n`
  })

  // 2) 全文转义
  text = escapeHtml(text)

  const lines = text.split('\n')
  const html = []
  let paragraph = []
  let listType = null // 'ul' | 'ol'
  let quoteLines = []

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${paragraph.map(inlineFormat).join('<br>')}</p>`)
      paragraph = []
    }
  }
  const flushList = () => {
    if (listType) {
      html.push(`</${listType}>`)
      listType = null
    }
  }
  const flushQuote = () => {
    if (quoteLines.length) {
      html.push(`<blockquote class="md-quote">${quoteLines.map(inlineFormat).join('<br>')}</blockquote>`)
      quoteLines = []
    }
  }
  const flushAll = () => {
    flushParagraph()
    flushList()
    flushQuote()
  }

  for (let i = 0; i < lines.length; i += 1) {
    const line = lines[i].replace(/\s+$/, '')
    const trimmed = line.trim()

    // 代码块占位符
    const placeholder = trimmed.match(/^\u0000MDBLOCK(\d+)\u0000$/)
    if (placeholder) {
      flushAll()
      html.push(codeBlocks[Number(placeholder[1])] || '')
      continue
    }

    if (!trimmed) {
      flushAll()
      continue
    }

    // 表格：当前行是 |...|，下一行是分隔行
    if (/^\|.*\|$/.test(trimmed) && i + 1 < lines.length && isTableSeparator(lines[i + 1].trim())) {
      flushAll()
      const header = splitTableRow(trimmed)
      const rows = []
      i += 1
      while (i + 1 < lines.length && /^\|.*\|$/.test(lines[i + 1].trim())) {
        i += 1
        rows.push(splitTableRow(lines[i].trim()))
      }
      html.push(buildTable(header, rows))
      continue
    }

    // 分隔线
    if (/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)) {
      flushAll()
      html.push('<hr class="md-hr">')
      continue
    }

    // 标题
    const heading = trimmed.match(/^(#{1,6})\s+(.*)$/)
    if (heading) {
      flushAll()
      const level = heading[1].length
      html.push(`<h${level} class="md-h md-h${level}">${inlineFormat(heading[2])}</h${level}>`)
      continue
    }

    // 引用（'>' 已被转义为 '&gt;'）
    const quote = trimmed.match(/^&gt;\s?(.*)$/)
    if (quote) {
      flushParagraph()
      flushList()
      quoteLines.push(quote[1])
      continue
    }

    // 无序列表
    const ulMatch = line.match(/^(\s*)[-*+]\s+(.*)$/)
    if (ulMatch) {
      flushParagraph()
      flushQuote()
      if (listType !== 'ul') {
        flushList()
        html.push('<ul class="md-list">')
        listType = 'ul'
      }
      html.push(`<li>${inlineFormat(ulMatch[2])}</li>`)
      continue
    }

    // 有序列表
    const olMatch = line.match(/^(\s*)\d+[.)]\s+(.*)$/)
    if (olMatch) {
      flushParagraph()
      flushQuote()
      if (listType !== 'ol') {
        flushList()
        html.push('<ol class="md-list">')
        listType = 'ol'
      }
      html.push(`<li>${inlineFormat(olMatch[2])}</li>`)
      continue
    }

    // 普通段落
    flushQuote()
    flushList()
    paragraph.push(trimmed)
  }

  flushAll()
  return html.join('\n')
}

/**
 * 把 Markdown 文本降级成纯文本（用于会话标题、日志摘要等）
 */
export function toPlainText(source, maxLength = 0) {
  let text = String(source || '')
    .replace(CODE_BLOCK_RE, ' ')
    .replace(/[#>*`_~\-|]/g, ' ')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim()
  if (maxLength > 0 && text.length > maxLength) text = `${text.slice(0, maxLength)}...`
  return text
}

export default renderMarkdown
