-- Phase 9+: Notable events — operator-curated timeline markers for the
-- almanac scrubber. These are real, dated world events that contextualise the
-- graph's history during time-travel. The operator adds to this table; the
-- frontend renders each as a waypoint on the scrubber track.
--
-- Seed rows are genuine, dated events (the design's fabricated "Hormuz tabling"
-- placeholder is intentionally omitted). Dates are the widely-recognised onset
-- of each event.

CREATE TABLE IF NOT EXISTS notable_events (
    event_id    UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    occurred_at TIMESTAMPTZ NOT NULL,
    label       TEXT        NOT NULL,
    description TEXT        NOT NULL DEFAULT '',
    category    TEXT,           -- geopolitical | economic | resource | technology | environmental
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_notable_events_occurred ON notable_events (occurred_at);

-- Idempotent seed key: an event is identified by its date + label.
CREATE UNIQUE INDEX IF NOT EXISTS idx_notable_events_unique
    ON notable_events (occurred_at, label);

INSERT INTO notable_events (occurred_at, label, description, category)
VALUES
    ('2022-02-24T00:00:00Z', 'Russia invades Ukraine',
     'Full-scale invasion; energy and grain shockwaves across Europe and global commodities.',
     'geopolitical'),

    ('2022-08-26T00:00:00Z', 'European energy crisis peak',
     'Dutch TTF gas benchmark peaks amid Russian supply cuts; industrial demand destruction.',
     'resource'),

    ('2023-03-10T00:00:00Z', 'Silicon Valley Bank collapse',
     'Second-largest US bank failure; regional-bank stress and a flight to quality.',
     'economic'),

    ('2023-05-25T00:00:00Z', 'AI capex inflection',
     'Nvidia''s data-centre guidance reframes the hyperscaler capital-expenditure cycle.',
     'technology'),

    ('2023-10-07T00:00:00Z', 'Israel–Gaza war begins',
     'Hamas attacks and the ensuing war reshape Middle East risk and energy premia.',
     'geopolitical'),

    ('2023-12-15T00:00:00Z', 'Red Sea shipping escalation',
     'Houthi attacks divert Suez traffic around the Cape; freight rates and lead times spike.',
     'resource'),

    ('2024-08-05T00:00:00Z', 'Yen carry-trade unwind',
     'A sharp JPY appreciation forces a global deleveraging of carry positions.',
     'economic')
ON CONFLICT (occurred_at, label) DO NOTHING;
