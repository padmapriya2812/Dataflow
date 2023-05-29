{\rtf1\ansi\ansicpg1252\cocoartf2638
\cocoatextscaling0\cocoaplatform0{\fonttbl\f0\fswiss\fcharset0 Helvetica;}
{\colortbl;\red255\green255\blue255;}
{\*\expandedcolortbl;;}
\paperw11900\paperh16840\margl1440\margr1440\vieww28600\viewh18000\viewkind0
\pard\tx566\tx1133\tx1700\tx2267\tx2834\tx3401\tx3968\tx4535\tx5102\tx5669\tx6236\tx6803\pardirnatural\partightenfactor0

\f0\fs24 \cf0 import argparse\
import time\
import logging\
import json\
import typing\
from datetime import datetime\
import apache_beam as beam\
from apache_beam.options.pipeline_options import GoogleCloudOptions\
from apache_beam.options.pipeline_options import PipelineOptions\
from apache_beam.options.pipeline_options import StandardOptions\
from apache_beam.transforms.combiners import CountCombineFn\
from apache_beam.runners import DataflowRunner, DirectRunner\
\
# ### functions and classes\
\
class CommonLog(typing.NamedTuple):\
    ip: str\
    user_id: str\
    lat: float\
    lng: float\
    timestamp: str\
    http_request: str\
    http_response: int\
    num_bytes: int\
    user_agent: str\
\
beam.coders.registry.register_coder(CommonLog, beam.coders.RowCoder)\
\
def parse_json(element):\
    row = json.loads(element.decode('utf-8'))\
    return CommonLog(**row)\
\
def add_processing_timestamp(element):\
    row = element._asdict()\
    row['event_timestamp'] = row.pop('timestamp')\
    row['processing_timestamp'] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")\
    return row\
\
class GetTimestampFn(beam.DoFn):\
    def process(self, element, window=beam.DoFn.WindowParam):\
        window_start = window.start.to_utc_datetime().strftime("%Y-%m-%dT%H:%M:%S")\
        output = \{'page_views': element, 'timestamp': window_start\}\
        yield output\
\
# ### main\
\
def run():\
    # Command line arguments\
    parser = argparse.ArgumentParser(description='Load from Json from Pub/Sub into BigQuery')\
    parser.add_argument('--project',required=True, help='Specify Google Cloud project')\
    parser.add_argument('--region', required=True, help='Specify Google Cloud region')\
    parser.add_argument('--staging_location', required=True, help='Specify Cloud Storage bucket for staging')\
    parser.add_argument('--temp_location', required=True, help='Specify Cloud Storage bucket for temp')\
    parser.add_argument('--runner', required=True, help='Specify Apache Beam Runner')\
    parser.add_argument('--input_topic', required=True, help='Input Pub/Sub Topic')\
    parser.add_argument('--agg_table_name', required=True, help='BigQuery table name for aggregate results')\
    parser.add_argument('--raw_table_name', required=True, help='BigQuery table name for raw inputs')\
    parser.add_argument('--window_duration', required=True, help='Window duration')\
\
    opts = parser.parse_args()\
\
    # Setting up the Beam pipeline options\
    options = PipelineOptions(save_main_session=True, streaming=True)\
    options.view_as(GoogleCloudOptions).project = opts.project\
    options.view_as(GoogleCloudOptions).region = opts.region\
    options.view_as(GoogleCloudOptions).staging_location = opts.staging_location\
    options.view_as(GoogleCloudOptions).temp_location = opts.temp_location\
    options.view_as(GoogleCloudOptions).job_name = '\{0\}\{1\}'.format('streaming-minute-traffic-pipeline-',time.time_ns())\
    options.view_as(StandardOptions).runner = opts.runner\
\
    input_topic = opts.input_topic\
    raw_table_name = opts.raw_table_name\
    agg_table_name = opts.agg_table_name\
    window_duration = opts.window_duration\
\
    # Table schema for BigQuery\
    agg_table_schema = \{\
        "fields": [\
            \{\
                "name": "page_views",\
                "type": "INTEGER"\
            \},\
            \{\
                "name": "timestamp",\
                "type": "STRING"\
            \},\
\
        ]\
    \}\
\
    raw_table_schema = \{\
        "fields": [\
            \{\
                "name": "ip",\
                "type": "STRING"\
            \},\
            \{\
                "name": "user_id",\
                "type": "STRING"\
            \},\
            \{\
                "name": "user_agent",\
                "type": "STRING"\
            \},\
            \{\
                "name": "lat",\
                "type": "FLOAT",\
                "mode": "NULLABLE"\
            \},\
            \{\
                "name": "lng",\
                "type": "FLOAT",\
                "mode": "NULLABLE"\
            \},\
            \{\
                "name": "event_timestamp",\
                "type": "STRING"\
            \},\
            \{\
                "name": "processing_timestamp",\
                "type": "STRING"\
            \},\
            \{\
                "name": "http_request",\
                "type": "STRING"\
            \},\
            \{\
                "name": "http_response",\
                "type": "INTEGER"\
            \},\
            \{\
                "name": "num_bytes",\
                "type": "INTEGER"\
            \}\
        ]\
    \}\
\
    # Create the pipeline\
    p = beam.Pipeline(options=options)\
\
\
\
    parsed_msgs = (p | 'ReadFromPubSub' >> beam.io.ReadFromPubSub(input_topic)\
                     | 'ParseJson' >> beam.Map(parse_json).with_output_types(CommonLog))\
\
    (parsed_msgs\
        | "AddProcessingTimestamp" >> beam.Map(add_processing_timestamp)\
        | 'WriteRawToBQ' >> beam.io.WriteToBigQuery(\
            raw_table_name,\
            schema=raw_table_schema,\
            create_disposition=beam.io.BigQueryDisposition.CREATE_IF_NEEDED,\
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND\
            )\
        )\
\
    (parsed_msgs\
        | "WindowByMinute" >> beam.WindowInto(beam.window.FixedWindows(60))\
        | "CountPerMinute" >> beam.CombineGlobally(CountCombineFn()).without_defaults()\
        | "AddWindowTimestamp" >> beam.ParDo(GetTimestampFn())\
        | 'WriteAggToBQ' >> beam.io.WriteToBigQuery(\
            agg_table_name,\
            schema=agg_table_schema,\
            create_disposition=beam.io.BigQueryDisposition.CREATE_IF_NEEDED,\
            write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND\
            )\
    )\
\
    logging.getLogger().setLevel(logging.INFO)\
    logging.info("Building pipeline ...")\
\
    p.run().wait_until_finish()\
\
if __name__ == '__main__':\
  run()}