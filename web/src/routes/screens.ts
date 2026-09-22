/** Екран operator GUI за §7.7 ТЗ. Маршрути й вміст додає WP-11C. */
export interface PlannedScreen {
  readonly id: string;
  readonly title: string;
  readonly summary: string;
}

/** Дев'ять екранів §7.7 у порядку специфікації. */
export const PLANNED_SCREENS: readonly PlannedScreen[] = [
  {
    id: 'overview',
    title: 'Огляд',
    summary: 'health компонентів, freshness/SLO, backlog черг, пули worker-ів, інциденти',
  },
  {
    id: 'sources',
    title: 'Джерела',
    summary: 'рейтинг і докази, стани джерел/маршрутів, розклад, rate budget, pause/resume',
  },
  {
    id: 'jobs',
    title: 'Jobs і помилки',
    summary: 'фільтри, lease/retries, dead letters, lineage, ідемпотентний replay',
  },
  {
    id: 'workers',
    title: 'Workers',
    summary: 'бажані/поточні replicas, heartbeats, leases, scale і drain у межах min/max',
  },
  {
    id: 'data',
    title: 'Дані',
    summary: 'новини з українським перекладом, каталоги/авто, продавці, raw lineage',
  },
  {
    id: 'matching',
    title: 'Matching',
    summary: 'кандидати й докази, merge/unmerge, ручне блокування, preview впливу',
  },
  {
    id: 'releases',
    title: 'Releases та експорти',
    summary: 'прогрес збірки, склад, якість, parts/checksums, download і verify',
  },
  {
    id: 'retention',
    title: 'Retention і capacity',
    summary: 'pins, compaction dry-run/apply, hot/cold bytes, прогноз і рекомендації',
  },
  {
    id: 'audit',
    title: 'Аудит',
    summary: 'хто, коли і що змінив, request/idempotency ID, before/after і результат',
  },
];
