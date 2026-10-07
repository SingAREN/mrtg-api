import urllib.parse
import urllib.request
import urllib.error
import os.path
import base64
import json
import datetime
import calendar
import csv
from werkzeug.middleware.proxy_fix import ProxyFix
from flask import Flask, jsonify, abort, make_response, request, render_template
from flask_caching import Cache
from flask_cors import CORS, cross_origin
from bs4 import BeautifulSoup


config = {
    'PREFERRED_URL_SCHEME': 'https',
    'DEBUG': False,
    'CACHE_TYPE': 'simple',
    'CACHE_DEFAULT_TIMEOUT': 300,
    'MRTG_BASE_DIR': '/opt/mrtg/data/',
    'MRTG_CONFIG': '/opt/mrtg/config/mrtg.cfg',
    'EDUROAM_STATS_BASE_URL': '',
    'MONTHS': {'01': 'Jan',
               '02': 'Feb',
               '03': 'Mar',
               '04': 'Apr',
               '05': 'May',
               '06': 'Jun',
               '07': 'Jul',
               '08': 'Aug',
               '09': 'Sep',
               '10': 'Oct',
               '11': 'Nov',
               '12': 'Dec'},
    'GROUPS': {
        'group1': {'mrtg': ['interface1'],
                   'eduroam': None },
        'group2': {'mrtg': ['interface1',
                          'interface2',
                          'interface3'],
                 'eduroam': ['institution1']}
    }
}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1)
app.config.from_mapping(config)
cache = Cache(app)
cors = CORS(app, resources=r'/eduroam/*')

def get_json_from_api(target_url):
    """
    Retrieves the JSON formatted data from this mrtg_api API.
    :param target_url: URL for retrieving JSON data from the MRTG API
    :return: JSON data from the MRTG API
    """
    try:
        api_response = urllib.request.urlopen(target_url)
    except urllib.error.HTTPError:
        return abort(404)
    data = json.loads(api_response.read())
    api_response.close()
    return data


def encode_mrtg_graph_to_base64(filename):
    """
    Retrieves target MRTG graph (PNG image) from the MRTG_BASE_DIR, encodes said image as base64 string,
    s followed by formatting it into appropriate src URL for HTML img tags.
    :param filename: target MRTG graph filename. e.g. for NSCC, filename will be 'nscc'
    :return: base64 encoded html img src URL
    """
    image_path = os.path.join(app.config['MRTG_BASE_DIR'], filename)
    with open(image_path, 'rb') as image_file:
        encoded_img = base64.b64encode(image_file.read())
    b64_image = 'data:image/png;base64,{}'.format(encoded_img.decode('utf-8'))
    return b64_image


def scrape_mrtg_html(beautiful_soup_object, target):
    """
    Crawls through a MRTG html page which is loaded as a Beautiful Soup 4 object.
    It looks for the following tags:
        1). <title> : Title of the MRTG Utilisation Page
        2). <strong> : Text within <strong> contains the last updated time for the MRTG graphs
        3). <img> :
            a) 'src=' element contains the relative path of the MRTG graph images
            b) 'alt=' element contains the graph period, e.g. 'day', 'week', etc
        4). <td> :  contains throughput data in the following format 'bit-rate (link utilisation percentage)'

    After every <img> tag, there will be a table where the <td> tags contain the utilisation data. The first three
    <td> tags after an <img> tag represent the incoming max, average and current utilisation values whilst
    the following three after the previous <td> tag represent the outgoing max, average and current utilisation values.

    The retrieved data will be stored as a dictionary and returned to the function that called it.

    :param beautiful_soup_object:
    :param target: String ID used to retrieve MRTG utilisation HTML page
    :return: dictionary containing MRTG utilisation data
    """
    mrtg_data = dict()
    html_tags = beautiful_soup_object.find_all(['img', 'td'])
    utilisation_titles = ['max', 'average', 'current']

    mrtg_data.update({'title': beautiful_soup_object.title.get_text(),
                      'last_update': beautiful_soup_object.strong.get_text()})

    for index in range(0, 22, 7):
        time_period = html_tags[index].get('alt')

        in_list = [table_tag.get_text().strip() for table_tag in html_tags[index+1:index+4]]
        in_dictionary = dict(zip(utilisation_titles, in_list))

        out_list = [table_tag.get_text().strip() for table_tag in html_tags[index+4:index+7]]
        out_dictionary = dict(zip(utilisation_titles, out_list))

        image_filename = '{}-{}.png'.format(target, time_period)
        image = encode_mrtg_graph_to_base64(image_filename)

        mrtg_data.update({time_period: {'in': in_dictionary,
                                        'out': out_dictionary,
                                        'img': image}})
    return mrtg_data


def get_api_urls_for_group(group, graph_type):
    """

    :param group:
    :param graph_type: mrtg or eduroam
    :return:
    """
    group_scan_api_url = os.path.join(request.url_root, '{}/'.format(graph_type))
    api_response = get_json_from_api(group_scan_api_url)
    if group == 'singaren':
        response = jsonify([(tag, url) for tag, url in api_response.items()])
        response.status_code = 200
        return response
    try:
        response = jsonify(list(map(lambda x: (x, api_response.get(x)), app.config['GROUPS'].get(group)[graph_type])))
    except TypeError:
        return abort(404, 'unable to retrieve data, group does not exist')
    response.status_code = 200
    return response


@app.errorhandler(400)
def bad_request(error):
    return make_response(jsonify({'error': error.description}), 400)


@app.errorhandler(403)
def forbidden(error):
    return make_response(jsonify({'error': error.description}), 403)


@app.errorhandler(404)
def not_found(error):
    return make_response(jsonify({'error': error.description}), 404)


@app.route("/mrtg/", methods=['GET'])
@cache.cached()
def scan_for_mrtg_graphs():
    """
    Scans for all active MRTG targets with the mrtg.cfg file
    :return: JSON formatted list of available MRTG targets
    """
    mrtg_entries = {}
    try:
        with open(app.config['MRTG_CONFIG'], 'r') as cfg_file:
            for line in cfg_file:
                if '#' in line.strip():
                    continue
                if 'Target' in line:
                    # Retrieve target name identifier
                    target = line.split('[')[1].split(']')[0].lower()
                    target_upcase = target.upper()
                    mrtg_entries.update({target_upcase: urllib.parse.urljoin(request.url, '{}/{}'.format(target, 'render'))})
    except FileNotFoundError:
        return abort(404, 'unable to retrieve data, file does not exist')
    resp = jsonify(mrtg_entries)
    resp.status_code = 200
    return resp


@app.route("/mrtg/<path:target>", methods=['GET'])
@cache.cached(timeout=300)
def get_mrtg_data(target):
    """
    Retrieves targeted MRTG utilisation page from MRTG_BASE_URL and scans said page for required MRTG data. The
    data is then presented to the user's browser in JSON format.
    If MRTG target does not exist, an error message will appear in JSON format.
    :param target: String ID used to retrieve MRTG utilisation HTML page
    :return: JSON formatted MRTG data
    """
    mrtg_url = urllib.parse.urljoin(app.config['MRTG_BASE_DIR'], '{target}{html}'.format(target=target,
                                                                                         html='.html'))
    try:
        parsed_html = BeautifulSoup(open(mrtg_url), 'lxml')
    except AttributeError:
        return abort(400, 'unable to retrieve data, source malformed')
    except FileNotFoundError:
        return abort(404, 'unable to retrieve data, file does not exist')
    json_store = scrape_mrtg_html(parsed_html, target)


    # Remove Percentage Values from in and out 
    timescale = ["day", "week", "month", "year"]
    in_out = ["in", "out"]
    throughput_values = ["average", "current", "max"]
    for times in timescale:
        for direction in in_out:
            for throughput in throughput_values: 
                json_store[times][direction][throughput] = json_store[times][direction][throughput].split(" (")[0]

    resp = jsonify(json_store)
    resp.status_code = 200
    return resp


@app.route('/mrtg/<path:target>/render', methods=['GET'])
@cache.cached()
def render_html_shell_template(target):
    """
    Renders HTML statistics page shell with data received from the API
    :param target: MRTG tag as placeholder value
    :return: Rendered HTML network statistics page
    """
    # Retrieves MRTG API URL by splitting off the URL tail, /render, thus leaving behind <url_root>/mrtg/<target>
    api_url = os.path.split(request.base_url)[0].lower()
    data = get_json_from_api(api_url)
    resp = render_template('network_utilisation_shell.html.j2', **data)
    return resp


@app.route('/mrtg/group/<path:group>', methods=['GET'])
@cache.cached()
def get_mrtg_urls_for_group(group):
    """
    Retrieve authorised MRTG graph URLs for a specific group
    :param group: insitutional tag e.g. mit
    :return: list of available URLs for specific group
    """
    return get_api_urls_for_group(group, 'mrtg')


@app.route('/eduroam/', methods=['GET'])
@cache.cached()
def scan_for_eduroam_ihls():
    """
    Retrieves all available IHL tags by scanning the Daily CSV file and saving all entries into a set.
    :return: JSON list of available IHL entries
    """
    month_abbr, year = datetime.datetime.strftime(datetime.datetime.now() - datetime.timedelta(1), '%b %Y').split()
    target = 'stats/Daily{}{}.csv'.format(month_abbr, year)
    target_url = urllib.parse.urljoin(app.config['EDUROAM_STATS_BASE_URL'], target)
    try:
        csv_response = urllib.request.urlopen(target_url)
    except urllib.error.HTTPError:
        return abort(404)

    decoded_csv = map(bytes.decode, csv_response)
    reader = csv.DictReader(decoded_csv, delimiter=',')
    ihl_set = {row['IHL'] for row in reader}
    ihls = {ihl: urllib.parse.urljoin(request.url, ihl.lower()) for ihl in ihl_set}
    csv_response.close()
    resp = jsonify(ihls)
    return resp


@app.route('/eduroam/group/<path:group>', methods=['GET'])
@cache.cached()
def get_eduroam_urls_for_group(group):
    """
    Retrieve authorised eduroam graph URLs for a specific group
    :param group: insitutional tag e.g. mit
    :return: list of available URLs for specific group
    """
    return get_api_urls_for_group(group, 'eduroam')


@app.route('/eduroam/<path:target>', methods=['GET'])
def render_eduroam_ihl_usage_html_shell_template(target):
    """
    Renders eduroam statistics HTML page shell using CSV data retrieved via app.config['EDUROAM_STATS_BASE_URL']
    :param target: IHL institution, e.g. NUS, NTU, etc
    :return: Rendered HTML eduroam statistics page
    """
    # Set target to upper-case as this has been within the ihlconfig part of eduroam_stats
    target = target.upper()

    day, month, year = datetime.datetime.strftime(datetime.datetime.now() - datetime.timedelta(1), '%d %m %Y').split()

    # Allow users to query different dates by adding in day, month and/or year variables within the URL
    # e.g. /eduraom/insead?day=04&month=02&year=2019 to query data from 4th February 2019 for INSEAD.
    day = request.args.get('day', default=day, type=str)
    month = request.args.get('month', default=month, type=str)
    year = request.args.get('year', default=year, type=str)

    # Sets abbreviated month from month variable, requires locale to be en.US
    month_abbr = app.config['MONTHS'][month]
    last_day_of_month = calendar.monthrange(int(year), int(month))[1]

    # Uses institutional template by default.
    # If target is SINGAREN or TOTAL, it will use the NRS server load template
    template = 'eduroam_usage_shell.html.j2'
    if target == 'SINGAREN' or target == 'TOTAL':
        template = 'eduroam_server_load.html.j2'

    resp = render_template(template,
                           eduroam_csv_url=app.config['EDUROAM_STATS_BASE_URL'],
                           institution=target,
                           url_root=request.url_root,
                           day=day,
                           last_day_of_month=last_day_of_month,
                           month_abbreviated_us_locale=month_abbr,
                           month_number=month,
                           short_year=year[2:],
                           full_year=year)
    return resp


if __name__ == '__main__':
    app.run(host='0.0.0.0')

