def serialize_query_response(rows, granularity):
    return {
        "granularity": granularity,
        "buckets": [
            {
                "bucket_start": row["bucket_start"].isoformat(),
                "distinct_users": row["distinct_users"],
                "presence_hours": row["presence_hours"],
            }
            for row in rows
        ],
    }
