import urllib


try:
    input = raw_input
except NameError:
    pass


def get_addon():
    pass


def get_translation(key):
    translation = {
        "30000": "Recherche",
        "30001": "Recherche :",
        "30100": "Télécharger",
        "30110": "Information",
        "30200": "Erreur!",
        "30300": "Information",
        "30301": "Lancement du téléchargement",
        "30302": "Fichier téléchargé avec succès",
        "30551": "Debut de la mise à jour",
        "30552": "Woob est maintenant à jour",
    }
    return translation.get(key)


def get_addon_dir():
    return "/home/benjamin"


def get_settings(key):
    settings = {"downloadPath": get_addon_dir(), "nbVideoPerBackend": "0", "nsfw": "False"}
    return settings.get(key)


def display_error(error):
    print("{}: {}".format("ERROR", error))


def display_info(msg):
    print("{}: {}".format("INFO", msg))


def parse_params(paramStr):

    paramDic = {}
    # Parameters are on the 3rd arg passed to the script
    if len(paramStr) > 1:
        paramStr = paramStr.replace("?", "")

        # Ignore last char if it is a '/'
        if paramStr[len(paramStr) - 1] == "/":
            paramStr = paramStr[0 : len(paramStr) - 2]

        # Processing each parameter splited on  '&'
        for param in paramStr.split("&"):
            try:
                # Spliting couple key/value
                key, value = param.split("=")
            except:
                key = param
                value = ""

            key = urllib.unquote_plus(key)
            value = urllib.unquote_plus(value)

            # Filling dictionnary
            paramDic[key] = value
    return paramDic


def ask_user(content, title):
    return input(title)


def create_param_url(paramsDic, quote_plus=False):

    # url = sys.argv[0]
    url = ""
    sep = "?"

    try:
        for param in paramsDic:
            if quote_plus:
                url = url + sep + urllib.quote_plus(param) + "=" + urllib.quote_plus(paramsDic[param])
            else:
                url = f"{url}{sep}{param}={paramsDic[param]}"

            sep = "&"
    except Exception as msg:
        display_error("create_param_url %s" % msg)
        url = None
    return url


def add_menu_item(params={}):
    print('{} => "{}"'.format(params.get("name"), create_param_url(params)))


def add_menu_link(params={}):
    print("[{}] {} ({})".format(params.get("id"), params.get("name"), params.get("url")))
    # print params.get('itemInfoLabels')
    # print params.get('c_items')


def end_of_directory(update=False):
    print("******************************************************")


def download_video(url, name, dir="./"):
    print(f"Downlaod a video {name} from {url}")
