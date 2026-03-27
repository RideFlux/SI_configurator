USER  = odin
DEST  = /home/odin/car_config

# VPN IP: 개발 머신 → odim
ODIM_VPN = 10.8.0.214
# 로컬망 IP: odim → odil / odic
ODIL     = 192.168.31.7
ODIC     = 192.168.31.8

TARBALL = car_config.tar.gz
FILES   = car_config.py lidar_configurator.py config.yml v7_lidar_config.yaml

# =============================================================================

.PHONY: pack deploy deploy-odim deploy-odil deploy-odic clean clean-remote

pack:
	tar -czf $(TARBALL) $(FILES)

deploy: deploy-odim deploy-odil deploy-odic

deploy-odim: pack
	scp $(TARBALL) $(USER)@$(ODIM_VPN):~
	ssh $(USER)@$(ODIM_VPN) \
	  "mkdir -p $(DEST) && tar -xzf ~/$(TARBALL) -C $(DEST) && python3 $(DEST)/car_config.py ODIM"

deploy-odil: pack
	scp $(TARBALL) $(USER)@$(ODIM_VPN):~
	ssh $(USER)@$(ODIM_VPN) \
	  "scp ~/$(TARBALL) $(USER)@$(ODIL):~ && \
	   ssh $(USER)@$(ODIL) 'mkdir -p $(DEST) && tar -xzf ~/$(TARBALL) -C $(DEST) && python3 $(DEST)/car_config.py ODIL'"

deploy-odic: pack
	scp $(TARBALL) $(USER)@$(ODIM_VPN):~
	ssh $(USER)@$(ODIM_VPN) \
	  "scp ~/$(TARBALL) $(USER)@$(ODIC):~ && \
	   ssh $(USER)@$(ODIC) 'mkdir -p $(DEST) && tar -xzf ~/$(TARBALL) -C $(DEST) && python3 $(DEST)/car_config.py ODIC'"

clean:
	rm -f $(TARBALL)

clean-remote:
	ssh $(USER)@$(ODIM_VPN) "rm -rf $(DEST) ~/$(TARBALL)"
	ssh $(USER)@$(ODIM_VPN) "ssh $(USER)@$(ODIL) 'rm -rf $(DEST) ~/$(TARBALL)'"
	ssh $(USER)@$(ODIM_VPN) "ssh $(USER)@$(ODIC) 'rm -rf $(DEST) ~/$(TARBALL)'"
