from odemis import model
sem = model.getComponent(role="SEM")

pressure = sem.get_pressure()
state = sem.get_vacuum_state()
print("The chamber is currently {} and the pressure is {} pascal".format(state,pressure))

if state = "pumped":
    vent_confirm = input("Chamber is pumped. Vent chamber? This will take some time (appr. 3 minutes). y/n: ")
    if vent_confirm = "y" or vent_confirm = "Y":
        print("Venting chamber...")
        sem.vent()
elif state = "vented":
    pump_conform = input("Chamber is vented. Pump chamber? This will take some time (appr. 3 minutes). y/n: ")
    if pump_confirm = "y" or pump_confirm = "Y":
        print("Pumping chamber...")
        sem.pump()

state = sem.get_vacuum_state()
pressure = sem.get_pressure()
if state = "pumped":
    print("The chamber is now pumped. The pressure is {} pascal".format(pressure))
elif state = "vented":
    print("The chamber is now vented. The pressure is {} pascal".format(pressure))