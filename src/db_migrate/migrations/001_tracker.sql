-- Application tracker (SDD §4.6, data model §6). Source of the CDC stream into curated.applications.
CREATE TABLE tracker.applications (
  application_id  bigserial PRIMARY KEY,
  refnr           text,
  company         text NOT NULL,
  role_title      text NOT NULL,
  applied_date    date NOT NULL,
  status          text NOT NULL CHECK (status IN
                   ('applied', 'screening', 'interview', 'offer', 'rejected', 'withdrawn', 'ghosted')),
  source          text,
  notes           text,
  updated_at      timestamptz NOT NULL DEFAULT now()
);

-- Full before-images so DMS can emit complete updates and deletes.
ALTER TABLE tracker.applications REPLICA IDENTITY FULL;
