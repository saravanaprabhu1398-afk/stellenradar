output "database_names" {
  value = { for k, db in aws_glue_catalog_database.layer : k => db.name }
}

output "athena_workgroup" {
  value = aws_athena_workgroup.this.name
}

output "raw_postings_table" {
  value = "${aws_glue_catalog_table.postings_lines.database_name}.${aws_glue_catalog_table.postings_lines.name}"
}
