{{ config(materialized='table') }}
select
    md5(cast(ensembl_transcript_id as varchar)) as transcript_sk,
    cast(ensembl_transcript_id as varchar) as ensembl_transcript_id,
    length_bp
from {{ ref('stg_transcript') }}
