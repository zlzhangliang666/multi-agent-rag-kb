/**
 * Mermaid 渲染封装
 *
 * - 使用动态 import，只有真正收到 image 事件时才加载 mermaid（减小首屏体积）
 * - mermaid.render 返回 { svg } 字符串，直接交给 v-html 渲染
 * - 渲染失败（语法错误）时清理 mermaid 残留在 body 下的临时节点，并把错误抛给调用方
 */

let mermaidLib = null
let initialized = false
let idSeed = 0

async function getMermaid() {
  if (!mermaidLib) {
    const mod = await import('mermaid')
    mermaidLib = mod && mod.default ? mod.default : mod
  }
  return mermaidLib
}

function removeLeftover(ids) {
  ids.forEach((id) => {
    const el = document.getElementById(id)
    if (el && el.parentNode) el.parentNode.removeChild(el)
  })
}

/**
 * 渲染 Mermaid 代码为 SVG 字符串
 * @param {string} code Mermaid 源码
 * @param {{theme?:string}} options 主题（default / dark / neutral / forest）
 * @returns {Promise<string>} svg 字符串
 */
export async function renderMermaid(code, options = {}) {
  const source = String(code || '').trim()
  if (!source) throw new Error('Mermaid 代码为空')

  const mermaid = await getMermaid()

  if (!initialized) {
    mermaid.initialize({
      startOnLoad: false,
      securityLevel: 'loose',
      theme: options.theme || 'default',
      fontFamily: '"Microsoft YaHei", "PingFang SC", Helvetica, Arial, sans-serif',
      flowchart: { htmlLabels: true, curve: 'basis', useMaxWidth: true },
      sequence: { useMaxWidth: true },
    })
    initialized = true
  }

  idSeed += 1
  const id = `mmd-${Date.now().toString(36)}-${idSeed}`

  try {
    const result = await mermaid.render(id, source)
    const svg = typeof result === 'string' ? result : result && result.svg
    if (!svg) throw new Error('Mermaid 未返回 SVG')
    return svg
  } catch (err) {
    const message = (err && (err.message || err.str)) || String(err)
    throw new Error(String(message).split('\n')[0].slice(0, 160))
  } finally {
    // mermaid 在渲染过程中会往 body 插入 id / d+id 的临时容器，统一清理
    removeLeftover([id, `d${id}`])
  }
}

/**
 * 仅校验 Mermaid 语法，不产出 SVG
 * @returns {Promise<{ok:boolean, error:string}>}
 */
export async function validateMermaid(code) {
  const source = String(code || '').trim()
  if (!source) return { ok: false, error: 'Mermaid 代码为空' }
  try {
    const mermaid = await getMermaid()
    if (typeof mermaid.parse === 'function') {
      await mermaid.parse(source)
    }
    return { ok: true, error: '' }
  } catch (err) {
    return { ok: false, error: (err && err.message) || String(err) }
  }
}

export default renderMermaid
