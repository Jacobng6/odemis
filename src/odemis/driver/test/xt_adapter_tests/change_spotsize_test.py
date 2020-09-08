from odemis import model
sem = model.getComponent(role="SEM")

spotsize = sem.get_ebeam_spotsize()
print("Spot size is currently: {}".format(spotsize))

new_spotsize = input("New spotsize: ")
sem.set_ebeam_spotsize(new_spotsize)
spotsize = sem.get_ebeam_spotsize()
print("Spotsize is now: {}".format(spotsize))

info = spotsize_info()
print(info)