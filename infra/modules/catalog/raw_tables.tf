# Raw ingest files: gzip JSON Lines written by the ingest Lambda (SDD §4.1). Each line is read as one string and
# parsed with json_extract in SQL, so changes in the BA response shape never break the table. Search and detail
# files share one prefix and are told apart with the "$path" pseudo-column.
resource "aws_glue_catalog_table" "postings_lines" {
  name          = "postings_lines"
  database_name = aws_glue_catalog_database.layer["raw"].name
  table_type    = "EXTERNAL_TABLE"
  description   = "Raw BA search and detail records, one JSON document per line"

  partition_keys {
    name = "ingest_date"
    type = "string"
  }

  partition_keys {
    name = "run_id"
    type = "string"
  }

  parameters = {
    EXTERNAL                               = "TRUE"
    "projection.enabled"                   = "true"
    "projection.ingest_date.type"          = "date"
    "projection.ingest_date.format"        = "yyyy-MM-dd"
    "projection.ingest_date.range"         = "2026-10-01,NOW"
    "projection.ingest_date.interval"      = "1"
    "projection.ingest_date.interval.unit" = "DAYS"
    # run_id is unbounded, so queries must name it (WHERE run_id = '...')
    "projection.run_id.type"    = "injected"
    "storage.location.template" = "s3://${var.data_bucket}/raw/postings/ingest_date=$${ingest_date}/run_id=$${run_id}/"
  }

  storage_descriptor {
    location      = "s3://${var.data_bucket}/raw/postings/"
    input_format  = "org.apache.hadoop.mapred.TextInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.serde2.lazy.LazySimpleSerDe"
      parameters = {
        # A control character that JSON never contains unescaped, so each line stays one column
        "field.delim" = "\u0001"
      }
    }

    columns {
      name = "line"
      type = "string"
    }
  }
}
