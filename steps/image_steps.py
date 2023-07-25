import docker


from behave import then


try:
    DOCKER_CLIENT = docker.Client(version="1.22")
except:
    DOCKER_CLIENT = docker.from_env(version="1.22").api


@then(u'the image should contain label {label}')
@then(u'the image should contain label {label} {check} value {value}')
def label_exists(context, label, check="with", value=None):
    metadata = DOCKER_CLIENT.inspect_image(context.config.userdata['IMAGE'])
    config = metadata['Config']

    try:
        labels = config['Labels']
    except KeyError:
        raise Exception("There are no labels in the %s image" % context.config.userdata['IMAGE'])

    try:
        actual_value = labels[label]
    except KeyError:
        raise Exception("Label %s was not found in the %s image" % (label, context.config.userdata['IMAGE']))

    if not value:
        return True

    if check == "with" and actual_value == value:
        return True
    elif check == "containing" and actual_value.find(value) >= 0:
        return True

    raise Exception("The %s label does not contain %s value, current value: %s" % (label, value, actual_value))


@then(u'image should contain {count} layers')
def check_layers_count(context, count):
    """
    This feature is used to test if the image contains specific number
    of layers. It's useful to test if squashing was executed properly.

    https://projects.engineering.redhat.com/browse/APPINFRAT-1097
    """
    history = DOCKER_CLIENT.history(context.config.userdata['IMAGE'])
    if len(history) == int(count):
        return True

    raise Exception("Image does not contain %s layers, current number of layers: %s" % (count, len(history)), history)
