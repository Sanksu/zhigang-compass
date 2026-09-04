/**
 * 结构化岗位定义展示块（admin 审核页共用）。
 *
 * 展示 RAG 阶段二 LLM 结构化生成的核心职责/典型行业应用场景
 * （契约 DiscoveryCandidateItem.definition_structured；未生成时整块隐藏）。
 */
interface StructuredDefinitionData {
  core_duties?: string[] | null
  typical_scenarios?: string[] | null
}

export function StructuredDefinition({ data }: { data?: StructuredDefinitionData | null }) {
  const duties = data?.core_duties ?? []
  const scenarios = data?.typical_scenarios ?? []
  if (duties.length === 0 && scenarios.length === 0) return null
  return (
    <div className="space-y-1.5">
      {duties.length > 0 && (
        <div>
          <div className="mb-0.5 font-medium text-ink">核心职责</div>
          <ul className="space-y-0.5">
            {duties.map((d, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-ink-faint select-none">·</span>
                <span>{d}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {scenarios.length > 0 && (
        <div>
          <div className="mb-0.5 font-medium text-ink">典型行业应用场景</div>
          <ul className="space-y-0.5">
            {scenarios.map((s, i) => (
              <li key={i} className="flex gap-1.5">
                <span className="text-ink-faint select-none">·</span>
                <span>{s}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
