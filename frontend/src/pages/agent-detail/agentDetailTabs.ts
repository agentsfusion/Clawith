export const AGENT_DETAIL_TABS = [
    'status',
    'aware',
    'mind',
    'tools',
    'skills',
    'relationships',
    'workspace',
    'chat',
    'activityLog',
    'approvals',
    // --- Evolver subsystem (evolver agents only; filtered in AgentDetailPage) ---
    'feedback',
    'health',
    'script',
    'jobs',
    'settings',
] as const;

export type AgentDetailTab = typeof AGENT_DETAIL_TABS[number];

export const AGENT_DETAIL_SETTINGS_TABS = AGENT_DETAIL_TABS.filter(
    (tab) => !['aware', 'workspace', 'chat', 'feedback', 'health', 'script', 'jobs'].includes(tab),
) as AgentDetailTab[];

export function isAgentDetailTab(value: string | null | undefined): value is AgentDetailTab {
    return !!value && AGENT_DETAIL_TABS.includes(value as AgentDetailTab);
}

export function isAgentDetailSettingsTab(value: string | null | undefined): value is AgentDetailTab {
    return !!value && AGENT_DETAIL_SETTINGS_TABS.includes(value as AgentDetailTab);
}
